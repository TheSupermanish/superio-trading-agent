/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static export: the page fetches /snapshot.json in the browser, so the
  // deployed site stays current as long as that file is refreshed. No server
  // runtime to keep alive, and it hosts anywhere.
  output: "export",
  images: { unoptimized: true },
};
export default nextConfig;
