"""Non-executable advisory actions for S3 Optimizer V1."""

from app.actions.registry import ActionMetadata, action_registry


ADVISORY_ACTIONS = (
    (
        "s3_review_unused_bucket",
        "Review Unused S3 Bucket",
        "Review deletion or retain the bucket in Deep Archive; this advisory does not delete anything.",
    ),
    (
        "s3_review_storage_class",
        "Review S3 Storage Class",
        "Review ranked storage-class scenarios; lifecycle rules should state ObjectSizeGreaterThan=131071.",
    ),
    (
        "s3_restore_to_standard",
        "Restore S3 Objects to Standard",
        "Advisory only: remove the lifecycle rule before CopyObject; for GLACIER/DEEP_ARCHIVE restore, then copy through S3 Batch Operations. Copy/restore are additional retrievals, Batch Operations job/per-object fees and early-delete charges may apply.",
    ),
    (
        "s3_write_direct_to_class",
        "Write New S3 Objects Directly to Class",
        "Advisory only: set x-amz-storage-class to STANDARD_IA, GLACIER_IR, GLACIER, or DEEP_ARCHIVE (not INTELLIGENT_TIERING); GLACIER/DEEP_ARCHIVE objects are immediately non-readable without RestoreObject, and an existing lifecycle rule remains a backstop for producers that omit the header.",
    ),
)


for action_key, name, description in ADVISORY_ACTIONS:
    action_registry.register(
        ActionMetadata(
            action_key=action_key,
            name=name,
            description=description,
            resource_types=["s3"],
            handler=None,
            executable=False,
        )
    )
