# Scan-only demo. Do not apply. Expected: nine governance violations.
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
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = "stackgen-governance-scan-only"
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_security_group" "app" {
  name        = "stackgen-governance-scan-only"
  description = "Scan fixture: public SSH"
  ingress {
    description = "Public SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
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
  multi_az                    = false
  auto_minor_version_upgrade  = false
  skip_final_snapshot         = true
}
