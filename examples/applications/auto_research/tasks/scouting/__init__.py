"""Scouting around the paper: where to submit it, how its topics trend, what happens
after it is out, and which new papers this author will find relevant. Plain code."""

from .published import after_release
from .personal import personal_rank
from .trends import topic_trends
from .venues import venue_pick

__all__ = ["after_release", "personal_rank", "topic_trends", "venue_pick"]
