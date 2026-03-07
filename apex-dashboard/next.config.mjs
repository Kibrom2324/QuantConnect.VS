/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    APEX_API_URL: process.env.APEX_API_URL ?? "http://localhost:8000",
    MLFLOW_API_URL: process.env.MLFLOW_API_URL ?? "http://localhost:5000",
    ALPACA_BASE_URL: process.env.ALPACA_BASE_URL ?? "https://paper-api.alpaca.markets",
  },
};

export default nextConfig;
