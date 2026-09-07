terraform {
  backend "s3" {
    bucket       = "starbase-tfstate-746669194590"
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true # S3-native locking; no DynamoDB lock table
  }
}
