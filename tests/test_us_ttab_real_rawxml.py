from pathlib import Path

from app.us_ttab.parser import iter_ttab_bundles


def _one(name: str):
    bundles = list(iter_ttab_bundles(Path("tests/fixtures", name)))
    assert len(bundles) == 1
    return bundles[0]


def test_real_layout_opposition_attributes_and_children() -> None:
    bundle = _one("us_ttab_real_opposition.xml")
    p = bundle.proceeding
    assert p.proceeding_number == "91301803"
    assert p.proceeding_type_code == "OPP"
    assert p.proceeding_type == "Opposition"
    assert p.status_code == "9"
    assert p.status_text == "Pending"
    assert p.interlocutory_attorney == "Test Attorney"
    assert p.paralegal_name == "Test Paralegal"
    assert [(x.side, x.party_id, x.party_name) for x in bundle.parties] == [
        ("PLAINTIFF", "P1", "Example Plaintiff LLC"),
        ("DEFENDANT", "D1", "Example Defendant Inc."),
    ]
    assert len(bundle.properties) == 2
    defendant = next(x for x in bundle.properties if x.party_side == "DEFENDANT")
    assert defendant.serial_number == "99026361"
    assert defendant.registration_number == "7000001"
    assert defendant.application_status_code == "604"
    assert defendant.application_status == "Registered"
    assert defendant.mark_text == ""
    assert defendant.mark_explanation == "EXAMPLE DEFENDANT MARK"
    assert defendant.trademark_gid == "GID-D1"
    assert len(bundle.docket_entries) == 2
    first = bundle.docket_entries[0]
    assert first.identifier == "6"
    assert first.entry_number == "6"
    assert first.object_id == "OBJ6"
    assert first.entry_code == "TRIAL"
    assert first.due_date_raw == "03/16/2026"
    assert first.history_text == "TRIAL DATES RESET"


def test_real_layout_cancellation_code_display_and_docket_attributes() -> None:
    bundle = _one("us_ttab_real_cancellation.xml")
    p = bundle.proceeding
    assert p.proceeding_number == "92090576"
    assert p.proceeding_type_code == "CAN"
    assert p.proceeding_type == "Cancellation"
    assert p.status_code == "9"
    assert p.status_text == "Pending"
    assert len(bundle.parties) == 2
    assert {x.side for x in bundle.parties} == {"PLAINTIFF", "DEFENDANT"}
    assert len(bundle.properties) == 2
    registered = next(x for x in bundle.properties if x.registration_number)
    assert registered.property_filing_code == "R"
    assert registered.application_status_code == "800"
    assert registered.application_status == "Registered"
    assert registered.trademark_gid == "GID-CD1"
    assert len(bundle.docket_entries) == 2
    assert bundle.docket_entries[0].entry_code == "SUBMITTED"
    assert bundle.docket_entries[0].history_text == "SUBMITTED FOR FINAL DECISION"


def test_real_layout_exparte_has_single_plaintiff_and_confidential_attribute() -> None:
    bundle = _one("us_ttab_real_exparte.xml")
    assert bundle.proceeding.proceeding_type_code == "EXA"
    assert bundle.proceeding.proceeding_type == "Ex Parte Appeal"
    assert len(bundle.parties) == 1
    assert bundle.parties[0].side == "PLAINTIFF"
    assert len(bundle.properties) == 1
    assert bundle.properties[0].application_status_code == "603"
    assert len(bundle.docket_entries) == 2
    assert bundle.docket_entries[0].confidential == "false"


def test_real_layout_extension_allows_missing_status_property_and_docket() -> None:
    bundle = _one("us_ttab_real_extension.xml")
    p = bundle.proceeding
    assert p.proceeding_type_code == "EXT"
    assert p.proceeding_type == "Extension of Time"
    assert p.filing_date is None
    assert p.status_code == ""
    assert p.status_text == ""
    assert len(bundle.parties) == 1
    party = bundle.parties[0]
    assert party.company == "Example Co"
    assert party.organization == "Example Org"
    assert party.granted_to_date_raw == "12/01/2026"
    assert bundle.properties == ()
    assert bundle.docket_entries == ()
