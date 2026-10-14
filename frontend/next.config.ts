import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // a self-contained server for the Docker image
  reactStrictMode: true,
  devIndicators: false,
};

export default nextConfig;
