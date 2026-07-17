import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;
const testMode = import.meta.env.MODE === "test";

export const cloudEnabled =
  !testMode && import.meta.env.VITE_CLOUD_MODE === "true" && Boolean(url && publishableKey);

export const supabase = url && publishableKey
  ? createClient(url, publishableKey, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null;

export const backendMode = cloudEnabled ? "Supabase cloud" : "Local demo";
