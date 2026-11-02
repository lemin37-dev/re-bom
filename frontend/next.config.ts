import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ['192.168.*.*', '10.*.*.*', '172.16.*.*'],
}

export default nextConfig
