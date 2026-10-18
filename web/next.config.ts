import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // We open the app at http://127.0.0.1:3001 (on Windows "localhost" adds ~2 s per request); Next 16 blocks dev
  // resources for any host other than localhost unless it's listed here, which silently stops hydration.
  allowedDevOrigins: ["127.0.0.1"],
  devIndicators: false, // keeps the dev badge off recordings; compile/runtime errors still show
  // NEXT_DIST lets a test build go to another folder without touching the running server's .next.
  distDir: process.env.NEXT_DIST || ".next",

  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
