import { cp, mkdir, writeFile } from "node:fs/promises";

await mkdir("dist/server", { recursive: true });
await mkdir("dist/.openai", { recursive: true });
await mkdir("dist/client", { recursive: true });
await cp(".openai/hosting.json", "dist/.openai/hosting.json");
await cp("dist/index.html", "dist/client/index.html");
await cp("dist/assets", "dist/client/assets", { recursive: true });
await cp("dist/robot-mark.svg", "dist/client/robot-mark.svg");
await cp("dist/_redirects", "dist/client/_redirects");

const worker = `
export default {
  async fetch(request, env) {
    const response = await env.ASSETS.fetch(request);
    if (response.status !== 404 || request.method !== "GET") return response;

    const acceptsHtml = request.headers.get("accept")?.includes("text/html");
    if (!acceptsHtml) return response;

    const fallback = new URL("/index.html", request.url);
    return env.ASSETS.fetch(new Request(fallback, request));
  },
};
`;

await writeFile("dist/server/index.js", worker.trimStart());
