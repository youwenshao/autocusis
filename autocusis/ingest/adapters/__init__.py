"""Community course-data source adapters."""

from .cutopia import iter_cutopia_file
from .eaglezhen import iter_eaglezhen_file
from .queuesis import iter_queuesis_file
from .types import CanonicalCourseTerm, CanonicalMeeting, CanonicalSection

__all__ = [
    "CanonicalCourseTerm",
    "CanonicalMeeting",
    "CanonicalSection",
    "iter_cutopia_file",
    "iter_eaglezhen_file",
    "iter_queuesis_file",
]
