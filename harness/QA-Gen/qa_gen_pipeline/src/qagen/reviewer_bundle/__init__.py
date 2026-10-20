"""Local-only reviewer bundle and loopback-controlled viewer bridge.

This package intentionally sits outside the solver-safe release exporter.  A
reviewer bundle contains selected anonymized scene bytes plus private scorer
and audit material on disk, while its HTTP surface exposes only the rebased
solver task projection and anonymous scene routes.
"""

from .bundle import ReviewerBundleError, build_reviewer_bundle, verify_reviewer_bundle
from .preproduction import (
    PendingReviewerBundleError,
    build_pending_reviewer_bundle,
    create_pending_reviewer_bundle_server,
    verify_pending_reviewer_bundle,
)
from .server import ReviewerBundleServerError, create_reviewer_bundle_server

__all__ = [
    "ReviewerBundleError",
    "PendingReviewerBundleError",
    "ReviewerBundleServerError",
    "build_pending_reviewer_bundle",
    "build_reviewer_bundle",
    "create_pending_reviewer_bundle_server",
    "create_reviewer_bundle_server",
    "verify_pending_reviewer_bundle",
    "verify_reviewer_bundle",
]
