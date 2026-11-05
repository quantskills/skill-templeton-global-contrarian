import ast
import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]

def functions(relative, names, **context):
    source=ast.parse((ROOT/relative).read_text(encoding="utf-8"))
    nodes=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0)]
    nodes.extend(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names)
    namespace={"pd":pd,"datetime":datetime,"sys":sys,**context}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),relative,"exec"),namespace)
    return namespace

class MigrationTests(unittest.TestCase):
    def test_a_share_failure_does_not_fall_back_to_hk_statement(self):
        sdk=Mock();sdk.get_fina_reports.side_effect=RuntimeError("unavailable")
        ns=functions("scripts/factor.py",{"_get_ttm_quarters","_find_column","get_financial_ttm"},panda_data=sdk)
        result=ns["get_financial_ttm"](["000001.SZ"],"20260630")
        self.assertTrue(result.empty)
        sdk.get_fina_statement.assert_not_called()

if __name__ == "__main__":
    unittest.main()
