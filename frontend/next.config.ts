import type { NextConfig } from "next";

/**
 * The browser never talks to the API directly.
 *
 * Requests go to /api/* on whatever host served the page, and Next proxies
 * them on to FastAPI. That means only one port has to be reachable, so the
 * board opens from another desk on the LAN without CORS or a rebuild - the
 * API address is read at run time, not baked into the bundle.
 *
 * "standalone" emits a self-contained server under .next/standalone with
 * only the node_modules it actually uses, which is what makes the offline
 * machine work: copy the folder, run node server.js, no registry needed.
 */

const API = process.env.ALGOE_API_URL ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1", "192.168.1.117"],
  rewrites: async () => [
    { source: "/api/:path*", destination: `${API}/api/:path*` },
  ],
};

export default nextConfig;
