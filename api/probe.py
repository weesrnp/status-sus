# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# A Vercel não garante a raiz do projeto no sys.path; sem isso "api._common" não importa.
ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from api._common import APIHandler  # noqa: E402


class handler(APIHandler):
    endpoint = 'probe'
