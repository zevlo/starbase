# Single-table design.
#   Launch:        pk=LAUNCH#<id>  sk=META      gsi1pk=LANE#UPCOMING|LANE#PAST  gsi1sk=<net>#<id>
#   Run marker:    pk=META#INGEST  sk=JOB#<job>
#   Call budget:   pk=RATE#LL2     sk=HOUR#<yyyy-mm-ddThh>
resource "aws_dynamodb_table" "launches" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "gsi1pk"
    type = "S"
  }

  attribute {
    name = "gsi1sk"
    type = "S"
  }

  # "Upcoming launches sorted by NET" in a single Query. Sparse: only launch items carry gsi1*.
  global_secondary_index {
    name            = "gsi1-lane-net"
    projection_type = "ALL"

    key_schema {
      attribute_name = "gsi1pk"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "gsi1sk"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
