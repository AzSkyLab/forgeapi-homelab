terraform {
  required_version = ">= 1.9"
}

variable "gate_root" {
  type = string
}

resource "terraform_data" "held_apply" {
  provisioner "local-exec" {
    command = "python3 ${path.module}/gate.py"
    environment = {
      GATE_ROOT = var.gate_root
    }
  }
}
