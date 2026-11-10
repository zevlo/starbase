# CAPCOM: the starbase conversationalist, as an AgentCore Runtime agent
# (direct code upload from agents/copilot, built by `make agent-zip`).
# Reads DynamoDB directly through the same Reader the read API uses — the
# copilot and the board cannot disagree about the data.

# -- code artifacts ---------------------------------------------------------
# AgentCore code configuration reads the zip from S3; versioning lets the
# runtime pin the exact uploaded version (new zip -> new version -> update).
resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name_prefix}-artifacts-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "copilot_zip" {
  bucket        = aws_s3_bucket.artifacts.id
  key           = "copilot/agent.zip"
  source        = "${path.module}/build/agent.zip"          # `make agent-zip` before plan/apply
  source_hash   = filemd5("${path.module}/build/agent.zip") # multipart uploads can't diff by etag
  content_type  = "application/zip"
  force_destroy = true
}

# -- execution role ----------------------------------------------------------
resource "aws_iam_role" "copilot" {
  name               = "${local.name_prefix}-copilot-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

data "aws_iam_policy_document" "copilot" {
  statement {
    sid     = "TableReads"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.launches.arn,
      "${aws_dynamodb_table.launches.arn}/index/gsi1-lane-net",
    ]
  }

  statement {
    sid = "BedrockInvoke"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*", # foundation-model ARNs carry no account segment
      "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
      "arn:aws:bedrock:*:${local.account_id}:applied-model/*",
    ]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:*:${local.account_id}:log-group:/aws/bedrock-agentcore/*",
      "arn:aws:logs:*:${local.account_id}:log-group:/aws/bedrock-agentcore/*:*",
    ]
  }
}

resource "aws_iam_role_policy" "copilot" {
  name   = "copilot"
  role   = aws_iam_role.copilot.id
  policy = data.aws_iam_policy_document.copilot.json
}

# -- the agent ---------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "copilot" {
  agent_runtime_name = "${local.name_prefix}_copilot" # a-z0-9_ only
  description        = "CAPCOM: answers visitor questions from the starbase table"
  role_arn           = aws_iam_role.copilot.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["agent.py"]
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket     = aws_s3_bucket.artifacts.id
          prefix     = aws_s3_object.copilot_zip.key
          version_id = aws_s3_object.copilot_zip.version_id
        }
      }
    }
  }

  environment_variables = {
    TABLE_NAME       = aws_dynamodb_table.launches.name
    BEDROCK_MODEL_ID = var.copilot_model_id
    AWS_REGION       = var.region
    LOG_LEVEL        = "INFO"
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = 600   # sessions survive a coffee break, not a day
    max_lifetime                 = 28800 # the service default; set explicitly or the provider cries bug
  }

  depends_on = [aws_iam_role_policy.copilot]
}
