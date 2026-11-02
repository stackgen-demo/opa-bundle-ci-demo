# Intentional negative demo: four RDS governance failures. Do not apply or merge.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "data" {
  bucket = "stackgen-governance-scan-only"
  tags = {
    Name        = "governance-fixture"
    Environment = "test"
    Owner       = "platform"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = "stackgen-governance-scan-only"
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_security_group" "app" {
  name        = "stackgen-governance-scan-only"
  description = "Scan fixture: private HTTPS"
  ingress {
    description = "Private HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  egress {
    description = "Private HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
}

resource "aws_db_instance" "app" {
  identifier                  = "stackgen-governance-scan-only"
  engine                      = "mysql"
  instance_class              = "db.t3.micro"
  allocated_storage           = 20
  username                    = "dbadmin"
  manage_master_user_password = true
  storage_encrypted           = false
  publicly_accessible         = true
  backup_retention_period     = 0
  deletion_protection         = false
  multi_az                    = true
  auto_minor_version_upgrade  = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "stackgen-governance-scan-only-final"
}
