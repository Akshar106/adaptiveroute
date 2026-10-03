# One image runs every process (api, worker, beat, migrations); the ECS task
# definitions differ only in the command.
resource "aws_ecr_repository" "app" {
  # One repository per account. If you run several environments in one account,
  # add the environment to the name.
  name = var.project_name

  # A tag (the git commit SHA) can never be pointed at different content, so
  # "which code is running?" always has one answer.
  image_tag_mutability = "IMMUTABLE"

  # Basic vulnerability scan of every pushed image (results in the ECR console).
  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the 20 most recent images (enough to roll back a long way) and expire the
# rest so storage cost stays flat.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 20 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}
