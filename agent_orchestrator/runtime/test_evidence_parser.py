"""
TestExecutionParser: Deterministic parser for test execution evidence.
Extracts individual test cases, durations, and outcomes (PASSED/FAILED/ERROR/SKIPPED)
from JUnit XML reports, Python unittest verbose output, and pytest console logs.
"""
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET


@dataclass
class TestCaseEvidence:
    test_id: str
    test_file: str = ""
    test_name: str = ""
    classname: str = ""
    status: str = "PASSED"  # PASSED | FAILED | ERROR | SKIPPED
    duration_seconds: float = 0.0
    message: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "test_file": self.test_file,
            "test_name": self.test_name,
            "classname": self.classname,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 4),
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class TestExecutionParser:
    """
    Parses test runner output into structured TestCaseEvidence objects.
    """

    @classmethod
    def parse(
        cls,
        stdout: str = "",
        stderr: str = "",
        xml_content: Optional[str] = None,
    ) -> List[TestCaseEvidence]:
        """
        Parses test results, prioritizing JUnit XML if provided, then falling back to
        stdout parsing (pytest or unittest -v formats).
        """
        results: List[TestCaseEvidence] = []

        # 1. Try JUnit XML if provided or found in stdout/stderr
        if xml_content:
            results = cls.parse_junit_xml(xml_content)
            if results:
                return results

        # Check if stdout or stderr contains embedded XML
        combined = f"{stdout}\n{stderr}"
        if "<testsuite" in combined or "<testsuites" in combined:
            xml_match = re.search(r"(<testsuite[s]?[\s\S]*?</testsuite[s]?>)", combined)
            if xml_match:
                results = cls.parse_junit_xml(xml_match.group(1))
                if results:
                    return results

        # 2. Try pytest console line parser: e.g. "tests/test_auth.py::TestAuth::test_login PASSED"
        results = cls.parse_pytest_stdout(stdout)
        if results:
            return results

        # 3. Try unittest verbose parser: e.g. "test_login (test_auth.TestAuth) ... ok"
        results = cls.parse_unittest_stdout(stdout)
        if results:
            return results

        return results

    @classmethod
    def parse_junit_xml(cls, xml_text: str) -> List[TestCaseEvidence]:
        """
        Parses standard JUnit XML from pytest, jest, maven, etc.
        """
        results: List[TestCaseEvidence] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return results

        testcases = root.findall(".//testcase")
        for tc in testcases:
            name = tc.get("name", "")
            classname = tc.get("classname", "")
            file_attr = tc.get("file", "")
            time_val = 0.0
            try:
                time_val = float(tc.get("time", "0.0"))
            except ValueError:
                pass

            status = "PASSED"
            message = None

            # Check failures
            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")

            if failure is not None:
                status = "FAILED"
                message = failure.get("message") or (failure.text or "").strip()
            elif error is not None:
                status = "ERROR"
                message = error.get("message") or (error.text or "").strip()
            elif skipped is not None:
                status = "SKIPPED"
                message = skipped.get("message") or (skipped.text or "").strip()

            test_id = f"{classname}.{name}" if classname else name
            if file_attr and file_attr not in test_id:
                test_id = f"{file_attr}::{test_id}"

            results.append(
                TestCaseEvidence(
                    test_id=test_id,
                    test_file=file_attr,
                    test_name=name,
                    classname=classname,
                    status=status,
                    duration_seconds=time_val,
                    message=message,
                )
            )

        return results

    @classmethod
    def parse_pytest_stdout(cls, stdout: str) -> List[TestCaseEvidence]:
        """
        Parses pytest console output lines like:
        tests/test_auth.py::test_login PASSED
        tests/test_auth.py::TestAuth::test_login PASSED [ 50%]
        tests/test_auth.py::test_fail FAILED
        tests/test_auth.py::test_skip SKIPPED
        tests/test_auth.py::test_err ERROR
        """
        results: List[TestCaseEvidence] = []
        # Pattern: filepath::(optional class::)test_name (PASSED|FAILED|ERROR|SKIPPED)
        pattern = re.compile(
            r"^(?P<path>[a-zA-Z0-9_\-/\\]+\.(?:py|js|ts))::(?P<name>[a-zA-Z0-9_\-]+(?:::?[a-zA-Z0-9_\-]+)*)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED)",
            re.MULTILINE | re.IGNORECASE,
        )

        for match in pattern.finditer(stdout):
            path = match.group("path").replace("\\", "/")
            full_name = match.group("name")
            raw_status = match.group("status").upper()

            parts = full_name.split("::")
            classname = parts[0] if len(parts) > 1 else ""
            test_name = parts[-1]

            test_id = f"{path}::{full_name}"

            results.append(
                TestCaseEvidence(
                    test_id=test_id,
                    test_file=path,
                    test_name=test_name,
                    classname=classname,
                    status=raw_status,
                )
            )

        return results

    @classmethod
    def parse_unittest_stdout(cls, stdout: str) -> List[TestCaseEvidence]:
        """
        Parses Python unittest -v console output lines like:
        test_login (test_auth.TestAuth) ... ok
        test_fail (test_auth.TestAuth) ... FAIL
        test_error (test_auth.TestAuth) ... ERROR
        test_skip (test_auth.TestAuth) ... skipped 'reason'
        """
        results: List[TestCaseEvidence] = []
        pattern = re.compile(
            r"^(?P<name>[a-zA-Z0-9_\-]+)\s+\((?P<class>[a-zA-Z0-9_\.]+)\)\s+\.\.\.\s+(?P<status>ok|FAIL|ERROR|skipped)",
            re.MULTILINE,
        )

        status_map = {
            "ok": "PASSED",
            "FAIL": "FAILED",
            "ERROR": "ERROR",
            "skipped": "SKIPPED",
        }

        for match in pattern.finditer(stdout):
            name = match.group("name")
            classname = match.group("class")
            raw_status = match.group("status")
            status = status_map.get(raw_status, "PASSED")

            test_id = f"{classname}.{name}"
            test_file = classname.split(".")[0] + ".py" if "." in classname else ""

            results.append(
                TestCaseEvidence(
                    test_id=test_id,
                    test_file=test_file,
                    test_name=name,
                    classname=classname,
                    status=status,
                )
            )

        return results
