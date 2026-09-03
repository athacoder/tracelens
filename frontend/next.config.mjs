/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard reads a running API; there is nothing to prerender at build
  // time and a build must not need a database.
  output: "standalone",
};

export default nextConfig;
