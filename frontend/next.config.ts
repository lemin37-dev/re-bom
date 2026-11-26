import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 컨테이너 이미지(infra/docker/web.Dockerfile)용: 실행에 필요한 파일만 .next/standalone 에 모은다
  output: 'standalone',
  allowedDevOrigins: ['192.168.*.*', '10.*.*.*', '172.16.*.*'],
}

export default nextConfig
