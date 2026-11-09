locals {
  buckets = {
    # 버킷 하나에 경로로 나눈다.
    #   bronze/  수집 원본, 사진 원본   (docs/data_contract.md 5·6절)
    #   state/   수집 상태              (src/rebom/ingestion/storage.py)
    #   silver/  정제 결과
    #   images/  서비스용 사진 WebP. CloudFront(OAC)는 이 경로만 읽는다
    data = "rebom-data-${var.account_id}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = false

  tags = {
    Name = each.value
    Role = each.key
  }
}

# 공개 접근은 버킷 단위로도 전부 막는다 (계정 단위 차단과 이중으로).
resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACL은 쓰지 않고 버킷 소유자가 모든 객체를 소유한다.
resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3(AES256). KMS 키는 요청마다 비용이 들어 쓰지 않는다.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# HTTPS가 아닌 요청은 거부한다.
data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# CloudFront(OAC)의 images/* 읽기 허용은 CloudFront를 만들 때 이 정책에 문장을 더한다.
resource "aws_s3_bucket_policy" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  policy = data.aws_iam_policy_document.tls_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.this]
}

# 데이터 버킷: 버전 관리로 실수로 덮어쓰거나 지운 원본·상태 파일을 되살린다.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.this["data"].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.this["data"].id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.data]
}
