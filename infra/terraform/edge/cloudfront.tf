# CloudFront 배포 하나로 웹을 밖에 연다. origin 은 둘이다.
#   기본, /api/*, /_next/static/*  -> k3s 서버 Traefik 80 (VPC origin, 프라이빗 IP 로만 들어감)
#   /images/*                      -> 데이터 버킷 images/ (OAC, 버킷은 계속 비공개)
# Traefik 이 /api 를 떼고 FastAPI 로, 나머지를 Next.js 로 보낸다 (infra/k8s/web/ingress.yaml).
# 뷰어 쪽은 HTTPS 만(HTTP 는 리다이렉트), CloudFront -> Traefik 구간은 VPC 안의 HTTP 다.

# ── origin 1: k3s 서버 (VPC origin) ──
# 서버 보안그룹에 바깥 인바운드를 열지 않고, CloudFront 가 VPC 안에 만든 ENI 로만 들어온다.
# 만드는 데 10~15분 걸린다.
resource "aws_cloudfront_vpc_origin" "server" {
  vpc_origin_endpoint_config {
    name                   = "rebom-dev-k3s-server"
    arn                    = data.aws_instance.server.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"

    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  tags = { Name = "rebom-dev-k3s-server" }
}

# VPC origin 을 만들면 AWS 가 이 VPC 에 CloudFront-VPCOrigins-Service-SG 를 만든다(AWS 관리, 수정 금지).
# 서버 보안그룹 80 은 이 보안그룹에서 오는 것만 받는다. CloudFront 관리형 프리픽스 목록으로 열면
# 서버의 퍼블릭 IP 로 다른 사람의 CloudFront 배포도 들어올 수 있어서 쓰지 않는다.
data "aws_security_group" "cloudfront_vpc_origin" {
  vpc_id = local.vpc_id

  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }

  depends_on = [aws_cloudfront_vpc_origin.server]
}

resource "aws_vpc_security_group_ingress_rule" "server_http_from_cloudfront" {
  security_group_id            = local.node_sg_id
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origin.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "Traefik 80 from CloudFront VPC origin only"
}

# ── origin 2: 데이터 버킷 images/ (OAC) ──
# 버킷 정책 허용 문장은 infra/terraform/storage 에 있다 (변수 images_cdn_distribution_arn).
resource "aws_cloudfront_origin_access_control" "images" {
  name                              = "rebom-dev-images"
  description                       = "rebom data bucket images/ for CloudFront"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── AWS 관리형 정책 ──
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# 쿼리스트링·쿠키·헤더를 다 넘기되 Host 는 origin 주소로 둔다. Traefik 규칙은 host 없이 경로로만 나눈다.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

locals {
  server_origin_id = "k3s-server"
  images_origin_id = "s3-images"
  all_methods      = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
}

resource "aws_cloudfront_distribution" "web" {
  enabled         = true
  comment         = "rebom dev web (k3s) + images (S3)"
  price_class     = var.price_class
  http_version    = "http2and3"
  is_ipv6_enabled = true

  origin {
    origin_id   = local.server_origin_id
    domain_name = data.aws_instance.server.private_dns

    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.server.id
      origin_read_timeout      = var.origin_read_timeout
      origin_keepalive_timeout = 5
    }
  }

  origin {
    origin_id                = local.images_origin_id
    domain_name              = local.data_bucket_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.images.id
  }

  # 웹 화면(Next.js). 페이지는 캐시하지 않는다.
  default_cache_behavior {
    target_origin_id           = local.server_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = local.all_methods
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # 검색 API·챗봇 (FastAPI). 캐시하지 않는다.
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = local.server_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = local.all_methods
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # Next.js 빌드 파일. 파일 이름에 해시가 붙어 있어 길게 캐시해도 된다.
  ordered_cache_behavior {
    path_pattern               = "/_next/static/*"
    target_origin_id           = local.server_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # 동물 사진 WebP. 키를 덮어쓰지 않으므로(storage README) 길게 캐시한다.
  ordered_cache_behavior {
    path_pattern               = "/images/*"
    target_origin_id           = local.images_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = false # WebP 는 이미 압축돼 있다
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # 도메인이 생기기 전까지는 *.cloudfront.net 기본 인증서를 쓴다.
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "rebom-dev-web" }

  # 서버 보안그룹 80 이 열리기 전에 배포가 켜지면 첫 요청이 504 로 끝난다.
  depends_on = [aws_vpc_security_group_ingress_rule.server_http_from_cloudfront]
}
