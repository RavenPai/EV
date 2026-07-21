import { spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cliScript = path.join(root, "node_modules", "supabase", "dist", "supabase.js");
if (!existsSync(cliScript)) {
  throw new Error("Supabase CLI is missing. Run npm install first.");
}

let integrationWorkdir;
let isolatedSupabaseDir;
const integrationTest = path.join(
  root,
  "supabase",
  "tests",
  "integration",
  "ingest-robot-message.test.mjs",
);
const testEnvFile = path.join(
  root,
  "supabase",
  "tests",
  "robot-ingest.test.env",
);
const ingestSecret = "local-integration-secret-not-for-production";
const excludedServices = [
  "imgproxy",
  "logflare",
  "mailpit",
  "realtime",
  "storage-api",
  "studio",
  "vector",
].join(",");

const childEnvironment = { ...process.env };
if (process.platform === "win32" && !childEnvironment.DOCKER_HOST) {
  const context = spawnSync(
    "docker",
    ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
    {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
      timeout: 15_000,
      windowsHide: true,
    },
  );
  const dockerHost = context.stdout?.trim();
  if (context.status === 0 && dockerHost) {
    childEnvironment.DOCKER_HOST = dockerHost;
  }
}

const run = (command, args, options = {}) => {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    env: { ...childEnvironment, ...options.env },
    encoding: "utf8",
    stdio: options.capture ? "pipe" : "inherit",
    timeout: options.timeout ?? 10 * 60_000,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture
      ? `\n${result.stdout ?? ""}\n${result.stderr ?? ""}`.trim()
      : "";
    throw new Error(
      `${path.basename(command)} ${args.join(" ")} failed with exit code ${result.status}.${detail}`,
    );
  }
  return result.stdout ?? "";
};

const runSupabase = (args, options = {}) => {
  if (!integrationWorkdir) {
    throw new Error("The isolated Supabase workdir has not been created.");
  }
  return run(
    process.execPath,
    [cliScript, ...args, "--workdir", integrationWorkdir],
    { ...options, cwd: integrationWorkdir },
  );
};

const findStatusValue = (value, acceptedKeys) => {
  if (!value || typeof value !== "object") return undefined;
  for (const [key, nested] of Object.entries(value)) {
    const normalized = key.toLowerCase().replaceAll(/[^a-z0-9]/g, "");
    if (acceptedKeys.includes(normalized) && typeof nested === "string") {
      return nested;
    }
    const found = findStatusValue(nested, acceptedKeys);
    if (found) return found;
  }
  return undefined;
};

const readLocalStatus = () => {
  const output = runSupabase(["status", "-o", "json"], {
    capture: true,
    timeout: 30_000,
  });
  const jsonStart = output.indexOf("{");
  if (jsonStart < 0) throw new Error("Supabase status did not return JSON.");
  const status = JSON.parse(output.slice(jsonStart));
  const apiUrl = findStatusValue(status, ["apiurl"]);
  const anonKey = findStatusValue(status, [
    "anonkey",
    "publishablekey",
    "publishable",
  ]);
  const serviceRoleKey = findStatusValue(status, [
    "servicerolekey",
    "servicekey",
    "secretkey",
  ]);
  if (!apiUrl || !anonKey || !serviceRoleKey) {
    throw new Error(
      "Supabase status is missing API_URL, ANON_KEY, or SERVICE_ROLE_KEY.",
    );
  }
  const hostname = new URL(apiUrl).hostname;
  if (!["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname)) {
    throw new Error(`Refusing to test against non-local Supabase URL: ${apiUrl}`);
  }
  return { apiUrl, anonKey, serviceRoleKey };
};

const localStackRunning = () => {
  const result = spawnSync(
    process.execPath,
    [
      cliScript,
      "status",
      "-o",
      "json",
      "--workdir",
      integrationWorkdir,
    ],
    {
      cwd: integrationWorkdir,
      env: childEnvironment,
      encoding: "utf8",
      stdio: "pipe",
      timeout: 30_000,
      windowsHide: true,
    },
  );
  return result.status === 0;
};

const waitForFunction = async (url, processLogs) => {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { method: "GET" });
      if (response.status === 405) return;
    } catch {
      // Edge Runtime is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(
    `Local ingest function did not become ready.\n${processLogs().slice(-4_000)}`,
  );
};

const stopProcessTree = (child) => {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
};

let startedStack = false;
let functionServer;
let functionLogs = "";
let cleanupStarted = false;

const cleanup = () => {
  if (cleanupStarted) return;
  cleanupStarted = true;

  stopProcessTree(functionServer);
  if (startedStack && integrationWorkdir) {
    try {
      runSupabase(["stop", "--no-backup"], { timeout: 2 * 60_000 });
    } catch (error) {
      console.error("Local Supabase cleanup failed:", error);
    }
  }

  if (!integrationWorkdir) return;
  const resolvedWorkdir = path.resolve(integrationWorkdir);
  const resolvedTemp = `${path.resolve(tmpdir())}${path.sep}`;
  if (
    resolvedWorkdir.startsWith(resolvedTemp) &&
    path.basename(resolvedWorkdir).startsWith(
      "miit-rover-supabase-integration-",
    )
  ) {
    rmSync(resolvedWorkdir, { recursive: true, force: true });
  } else {
    console.error(
      `Refusing to remove unexpected integration directory: ${resolvedWorkdir}`,
    );
  }
};

const handleSignal = (signal) => {
  console.error(`Received ${signal}; cleaning up the local test stack...`);
  cleanup();
  process.exit(signal === "SIGINT" ? 130 : 143);
};
const handleSigint = () => handleSignal("SIGINT");
const handleSigterm = () => handleSignal("SIGTERM");
process.once("SIGINT", handleSigint);
process.once("SIGTERM", handleSigterm);

try {
  integrationWorkdir = mkdtempSync(
    path.join(tmpdir(), "miit-rover-supabase-integration-"),
  );
  isolatedSupabaseDir = path.join(integrationWorkdir, "supabase");
  cpSync(path.join(root, "supabase"), isolatedSupabaseDir, {
    recursive: true,
    filter: (source) =>
      path.resolve(source) !== path.resolve(root, "supabase", ".temp"),
  });

  if (!localStackRunning()) {
    console.log("Starting the local miit-rover-integration Supabase stack...");
    startedStack = true;
    runSupabase(["start", "--exclude", excludedServices]);
  }

  console.log("Resetting the local database to the committed migrations...");
  runSupabase(["db", "reset", "--local", "--no-seed"]);

  console.log("Linting the local public schema...");
  runSupabase([
    "db",
    "lint",
    "--local",
    "--schema",
    "public",
    "--level",
    "error",
    "--fail-on",
    "error",
  ]);

  console.log("Running transactional pgTAP integration tests...");
  runSupabase([
    "test",
    "db",
    "--local",
    path.join(isolatedSupabaseDir, "tests", "database"),
  ]);

  const { apiUrl, anonKey, serviceRoleKey } = readLocalStatus();
  const functionUrl = `${apiUrl}/functions/v1/ingest-robot-message`;

  console.log("Starting the local robot-ingestion Edge Function...");
  functionServer = spawn(
    process.execPath,
    [
      cliScript,
      "functions",
      "serve",
      "ingest-robot-message",
      "--no-verify-jwt",
      "--env-file",
      testEnvFile,
      "--workdir",
      integrationWorkdir,
    ],
    {
      cwd: integrationWorkdir,
      env: {
        ...childEnvironment,
        SUPABASE_URL: apiUrl,
        SUPABASE_ANON_KEY: anonKey,
        SUPABASE_SERVICE_ROLE_KEY: serviceRoleKey,
        ROBOT_INGEST_SECRET: ingestSecret,
      },
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
      windowsHide: true,
    },
  );
  functionServer.stdout.on("data", (chunk) => {
    functionLogs += chunk.toString();
  });
  functionServer.stderr.on("data", (chunk) => {
    functionLogs += chunk.toString();
  });

  await waitForFunction(functionUrl, () => functionLogs);

  console.log("Running EMQX webhook contract integration tests...");
  run(
    process.execPath,
    ["--test", "--test-concurrency=1", integrationTest],
    {
      env: {
        SUPABASE_URL: apiUrl,
        SUPABASE_ANON_KEY: anonKey,
        SUPABASE_SERVICE_ROLE_KEY: serviceRoleKey,
        SUPABASE_FUNCTION_URL: functionUrl,
        ROBOT_INGEST_SECRET: ingestSecret,
      },
      timeout: 5 * 60_000,
    },
  );

  console.log("Supabase/EMQX integration tests passed.");
} catch (error) {
  if (functionLogs) {
    console.error("\nRecent Edge Function output:\n", functionLogs.slice(-4_000));
  }
  throw error;
} finally {
  process.off("SIGINT", handleSigint);
  process.off("SIGTERM", handleSigterm);
  cleanup();
}
