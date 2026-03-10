import os
from zipfile import ZipFile
from loguru import logger


def extract_mission(zip_path: str, member_name: str, output_path: str) -> str | None:
    """
    Extracts a specific member from a zip file to a new path.
    This is a minimal and corrected version of the previous function.
    """
    extracted_file_path = os.path.join(output_path, member_name)

    try:
        with ZipFile(zip_path, "r") as archive:
            archive.extract(member=member_name, path=output_path)

            logger.info(f"Extracted mission {member_name} to {output_path}")
            return extracted_file_path
    except Exception as e:
        logger.error(e)
        return None
