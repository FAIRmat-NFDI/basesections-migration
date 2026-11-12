"""Inputs and results shared by the BaseSection migration action."""

from typing import Literal

from pydantic import BaseModel, Field


class EntryRef(BaseModel):
    entry_id: str
    upload_id: str
    mainfile: str
    mainfile_os_path: str


class MigrationEntry(EntryRef):
    entry_data_m_def: str | None = Field(
        default=None, description='Original, pre-migration data.m_def of the entry.'
    )


class MigrationActionInput(BaseModel):
    user_id: str = Field(description='ID of the administrator starting the action.')
    upload_id: str = Field(description='ID of the administrator-owned report upload.')
    target_upload_ids: list[str] = Field(
        default_factory=list,
        description='IDs of the upload to migrate. If empty, all available staging '
        'uploads will be migrated.',
    )


class FindEntriesInput(BaseModel):
    user_id: str
    target_upload_ids: list[str] = Field(default_factory=list)


class FindReferencingEntriesInput(BaseModel):
    user_id: str
    target_entry_ids: list[str] = Field(
        description='IDs of target entries used to find unpublished source entries '
        'that reference any of them.'
    )


class FixReferencePathsInput(BaseModel):
    source_entry_id: str = Field(
        description='ID of the source entry whose reference paths need repair.'
    )
    target_entry_m_defs_path: str = Field(
        description='Path to a JSON mapping from target entry IDs to their original, '
        'pre-migration data.m_def values.'
    )


class TransformUploadInput(BaseModel):
    target_upload_id: str = Field(
        description='ID of the upload containing the entries to transform.'
    )
    entries: list[MigrationEntry] = Field(
        description='Entries and resolved mainfile paths to transform.'
    )


class TransformEntryResult(BaseModel):
    entry_ref: MigrationEntry
    status: Literal['transformed', 'skipped', 'failed']
    reason: str | None = None


class TransformUploadResult(BaseModel):
    transformed: list[MigrationEntry] = Field(default_factory=list)
    skipped: list[TransformEntryResult] = Field(default_factory=list)
    failed: list[TransformEntryResult] = Field(default_factory=list)


class ReportInput(BaseModel):
    user_id: str
    upload_id: str
    workflow_id: str
    started_at: str
    found: list[MigrationEntry]
    transformed: list[MigrationEntry]
    skipped: list[TransformEntryResult]
    failed: list[TransformEntryResult]
    error: str | None = None


class MigrationActionOutput(BaseModel):
    found_count: int
    transformed: list[MigrationEntry]
    skipped_count: int
    failed_count: int
    report_path: str
