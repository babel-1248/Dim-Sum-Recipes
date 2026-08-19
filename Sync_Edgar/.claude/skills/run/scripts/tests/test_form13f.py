import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from form13f import information_table_to_markdown  # noqa: E402
from convert import convert_13f, filing_documents, information_table_candidates  # noqa: E402


CURRENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALPHA | INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>012345678</cusip>
    <figi>BBG000ALPHA</figi>
    <value>2500000</value>
    <shrsOrPrnAmt><sshPrnamt>10000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>Call</putCall>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority><Sole>10000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>BETA CORP</nameOfIssuer>
    <titleOfClass>CL A</titleOfClass>
    <cusip>987654321</cusip>
    <value>500000</value>
    <shrsOrPrnAmt><sshPrnamt>2000.5</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <otherManager>1,10</otherManager>
    <votingAuthority><Sole>0</Sole><Shared>2000.5</Shared><None>0</None></votingAuthority>
  </infoTable>
</informationTable>
"""


class Form13FTests(unittest.TestCase):
    def test_current_schema_parses_every_row_and_renders_complete_table(self):
        markdown, warnings, facts = information_table_to_markdown(
            CURRENT_XML, filed="2026-05-15")

        self.assertEqual([], warnings)
        self.assertEqual(2, facts["entry_count"])
        self.assertEqual(3_000_000, facts["total_value_usd"])
        self.assertEqual("USD", facts["reported_value_unit"])
        self.assertEqual("012345678", facts["holdings"][0]["cusip"])
        self.assertEqual("BBG000ALPHA", facts["holdings"][0]["figi"])
        self.assertEqual("Call", facts["holdings"][0]["put_call"])
        self.assertEqual(2_000.5, facts["holdings"][1]["voting_shared"])
        self.assertIn("ALPHA \\| INC", markdown)
        self.assertIn("83.33%", markdown)
        self.assertIn("No quarter-to-quarter changes", markdown)

    def test_pre_2023_values_are_normalized_from_thousands(self):
        markdown, warnings, facts = information_table_to_markdown(
            CURRENT_XML, filed="2022-11-14")

        self.assertEqual([], warnings)
        self.assertEqual("USD thousands", facts["reported_value_unit"])
        self.assertEqual(3_000_000_000, facts["total_value_usd"])
        self.assertIn("$3,000,000,000 total reported value", markdown)

    def test_non_information_xml_is_rejected(self):
        markdown, warnings, facts = information_table_to_markdown(
            b"<edgarSubmission/>", filed="2026-05-15")

        self.assertEqual("", markdown)
        self.assertEqual({}, facts)
        self.assertIn("not a 13F information table", warnings[0])

    def test_filing_date_is_required_for_value_normalization(self):
        markdown, warnings, facts = information_table_to_markdown(CURRENT_XML)

        self.assertEqual("", markdown)
        self.assertEqual({}, facts)
        self.assertIn("filing date is required", warnings[0])

    def test_converter_finds_rendered_information_table_and_uses_raw_xml(self):
        docs = [
            ("13F-HR", "xslForm13F_X02/cover.xml", "Form 13F cover page"),
            ("INFORMATION TABLE", "xslForm13F_X02/holdings.xml", "Information Table"),
        ]
        self.assertEqual(
            "holdings.xml", information_table_candidates(docs, {
                "primary_document": "xslForm13F_X02/cover.xml",
                "raw_document": "cover.xml",
            })[0][0])

        class FakeClient:
            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)
                return CURRENT_XML

        client = FakeClient()
        markdown, warnings, facts = convert_13f(client, {
            "base_url": "https://www.sec.gov/Archives/edgar/data/1/accession",
            "filed": "2026-05-15",
            "primary_document": "xslForm13F_X02/cover.xml",
            "raw_document": "cover.xml",
        }, docs)

        self.assertEqual([], warnings)
        self.assertEqual(2, facts["entry_count"])
        self.assertEqual("holdings.xml", facts["information_table_file"])
        self.assertEqual([
            "https://www.sec.gov/Archives/edgar/data/1/accession/holdings.xml"
        ], client.urls)
        self.assertIn("Form 13F holdings", markdown)

    def test_sec_header_document_type_is_discovered_without_description(self):
        class HeaderClient:
            def get(self, _url):
                return (b"<DOCUMENT>\n<TYPE>13F-HR\n<SEQUENCE>1\n"
                        b"<FILENAME>primary_doc.xml\n<TEXT>\n</DOCUMENT>\n"
                        b"<DOCUMENT>\n<TYPE>INFORMATION TABLE\n<SEQUENCE>2\n"
                        b"<FILENAME>infotable.xml\n<TEXT>\n</DOCUMENT>")

        docs = filing_documents(HeaderClient(), {"header_url": "https://www.sec.gov/header"})

        self.assertEqual(("INFORMATION TABLE", "infotable.xml", ""), docs[1])


if __name__ == "__main__":
    unittest.main()
