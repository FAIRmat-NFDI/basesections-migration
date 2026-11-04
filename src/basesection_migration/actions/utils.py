from nomad.files import StagingUploadFiles
from nomad.processing.data import Upload


def get_staging_upload_files(upload_id) -> StagingUploadFiles:
    upload = Upload.get(upload_id)
    if upload is None:
        raise ValueError(f'Report upload {upload_id} does not exist.')
    if upload.published or not StagingUploadFiles.exists_for(upload_id):
        raise ValueError('The report upload must be an unpublished staging upload.')
    return StagingUploadFiles(upload_id)
