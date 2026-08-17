type SupabaseUrlOptions = {
  configuredUrl?: string;
  proxyPath?: string;
  browserOrigin?: string;
  production: boolean;
};

export const resolveSupabaseUrl = ({
  configuredUrl,
  proxyPath,
  browserOrigin,
  production,
}: SupabaseUrlOptions): string | undefined => {
  if (browserOrigin) {
    const origin = new URL(browserOrigin);
    const normalizedProxyPath = proxyPath?.trim();
    const useWorkersDevProxy =
      production && origin.hostname.endsWith(".workers.dev");

    if (normalizedProxyPath || useWorkersDevProxy) {
      return new URL(normalizedProxyPath || "/supabase", origin)
        .toString()
        .replace(/\/$/, "");
    }
  }

  return configuredUrl;
};
