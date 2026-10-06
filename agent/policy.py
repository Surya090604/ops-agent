"""Company policy: what the agent may do. Enforced in code, outside the model's control."""
import json
from urllib.parse import urlparse


class Policy:
    def __init__(self, cfg: dict):
        self.allowed = cfg.get("post_allowed_prefixes", [])
        self.threshold = cfg.get("approval_required_over_amount")

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f).get("policy", {}))

    def check(self, tool: str, args: dict):
        """-> (verdict, reason). verdict: deny | approve | approve_high"""
        if tool != "http_post":
            return "approve", ""
        path = urlparse(args.get("url", "")).path
        if not any(path.startswith(p) for p in self.allowed):
            return "deny", f"writes to '{path}' are not permitted (allowed: {self.allowed})"
        try:
            amt = float(str(args.get("fields", {}).get("amount", "0")).replace(",", "").replace("$", ""))
        except ValueError:
            amt = 0.0
        if self.threshold is not None and amt > self.threshold:
            return "approve_high", f"amount {amt:,.2f} exceeds the {self.threshold:,} auto-approval limit"
        return "approve", ""
