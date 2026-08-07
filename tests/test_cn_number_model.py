from app.cn.text import application_number_parts


def test_direct_cn_derived_number():
    parts = application_number_parts("12345678A")
    assert parts.full == "12345678A"
    assert parts.family_root == "12345678"
    assert parts.suffix_path == "A"
    assert parts.filing_route == "CN_DIRECT"
    assert parts.number_family == "CN_DIRECT_NUMBER"
    assert parts.international_registration_number == ""
    assert parts.is_derived_case is True


def test_madrid_designation_is_still_cn_case():
    parts = application_number_parts("G602365A")
    assert parts.full == "G602365A"
    assert parts.family_root == "G602365"
    assert parts.suffix_path == "A"
    assert parts.filing_route == "MADRID_DESIGNATION_CN"
    assert parts.number_family == "CN_MADRID_G_NUMBER"
    assert parts.international_registration_number == "602365"
    assert parts.is_derived_case is True


def test_madrid_root_without_suffix():
    parts = application_number_parts("G602365")
    assert parts.family_root == "G602365"
    assert parts.suffix_path == ""
    assert parts.international_registration_number == "602365"
    assert parts.is_derived_case is False
