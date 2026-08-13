from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata


@dataclass(frozen=True)
class SourceCatalogEntry:
    source_name: str
    scope_label: str
    segment: str
    default_country_code: str


def normalize_source_name(value: str) -> str:
    name = Path(str(value or "")).name
    text = unicodedata.normalize("NFKC", name).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


_SPECIFIC_NON_CN = {'agent_阿尔巴尼亚代理.xlsx': ('阿尔巴尼亚', 'AL'),
 'agent_Algeria agent list.html': ('阿尔及利亚', 'DZ'),
 'agent_List of Attorneys - U.S. Embassy in Argentina.pdf': ('阿根廷', 'AR'),
 'agent_阿根廷代理.xlsx': ('阿根廷', 'AR'),
 'agent_Aruba agent list.html': ('阿鲁巴', 'AW'),
 'agent_list_attorney_Egypt_a.pdf': ('埃及', 'EG'),
 'agent_Legal Assistance - U.S. Embassy in Estonia.pdf': ('爱沙尼亚', 'EE'),
 'agent_Antigua agent list.html': ('安提瓜', 'AG'),
 'agent_Legal Assistance - U.S. Embassy in Austria.pdf': ('澳大利亚', 'AU'),
 'agent_ttipa_ip_attorneys.xlsx': ('澳大利亚', 'AU'),
 'agent_Attorneys-list-for-Pakistan.pdf': ('巴基斯坦', 'PK'),
 'agent_Legal Assistance - U.S. Embassy in Bahrain.pdf': ('巴林', 'BH'),
 'agent_Legal Assistance - U.S. Embassy & Consulates in Brazil.pdf': ('巴西', 'BR'),
 'agent_Belarus agent list.html': ('白俄罗斯', 'BY'),
 'agent_Iceland agent list.html': ('冰岛', 'IS'),
 'agent_Legal Assistance - U.S. Embassy in Bosnia and Herzegovina.pdf': ('波黑', 'BA'),
 'agent_ATTORNEY-LIST-FOR-BELIZE-2021.pdf': ('伯利兹', 'BZ'),
 'agent_Attorneys in Paris - U.S. Embassy & Consulates in France.pdf': ('法国', 'FR'),
 'agent_Attorneys-List-for-Luanda-Feb-2023.pdf': ('法国', 'FR'),
 'agent_法国代理人列表.pdf': ('法国', 'FR'),
 'agent_kpaa_korean_patent_attorneys_with_contacts.xlsx': ('韩国', 'KR'),
 'agent_韩国代理.xls': ('韩国', 'KR'),
 'agent_柬埔寨代理.xls': ('柬埔寨', 'KH'),
 'agent_柬埔寨代理人A.pdf': ('柬埔寨', 'KH'),
 'agent_柬埔寨代理人B.pdf': ('柬埔寨', 'KH'),
 'agent_柬埔寨代理人C.pdf': ('柬埔寨', 'KH'),
 'agent_Kosovo agent list.pdf': ('科索沃', 'XK'),
 'agent_KE AGENT 2024 官网.josn': ('肯尼亚', 'KE'),
 'agent_KE AGENT 2024 官网.pdf': ('肯尼亚', 'KE'),
 'agent_KE ATTORNEYS 美国大使馆.docx': ('肯尼亚', 'KE'),
 'agent_Lawyer-List-in-Laos-March-2024.pdf': ('老挝', 'LA'),
 'agent_Madagascar agent list.html': ('马达加斯加', 'MG'),
 'agent_马来西亚代理列表.xlsx': ('马来西亚', 'MY'),
 'agent_马来西亚代理人.pdf': ('马来西亚', 'MY'),
 'agent_US practitioner 官网.txt': ('美国', 'US'),
 'agent_us_活跃代理.csv': ('美国', 'US'),
 'agent_us_活跃代理_11.csv': ('美国', 'US'),
 'agent_美国代理.csv': ('美国', 'US'),
 'agent_美国代理TSDR.xlsx': ('美国', 'US'),
 'agent_美国申请人.csv': ('美国', 'US'),
 'agent_蒙古代理官方列表.xlsx': ('蒙古', 'MN'),
 'agent_缅甸代理人A.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人B.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人C.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人D.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人E.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人F.pdf': ('缅甸', 'MM'),
 'agent_缅甸代理人G.pdf': ('缅甸', 'MM'),
 'agent_Legal Assistance - U.S. Embassy in Moldova.pdf': ('摩尔多瓦', 'MD'),
 'agent_Moldova agent list.html': ('摩尔多瓦', 'MD'),
 'agent_Legal Assistance - U.S. Embassy & Consulate in Morocco.pdf': ('摩洛哥', 'MA'),
 'agent_摩洛哥代理.csv': ('摩洛哥', 'MA'),
 'agent_摩洛哥代理.xlsx': ('摩洛哥', 'MA'),
 'agent_莫桑比克135家代理名单-来自官网.pdf': ('莫桑比克', 'MZ'),
 'agent_莫桑比克代理名单-来自官网.xlsx': ('莫桑比克', 'MZ'),
 'agent_ng Agents 官网.pdf': ('尼日利亚', 'NG'),
 'agent_Portugal agent list.html': ('葡萄牙', 'PT'),
 'agent_日本代理.xls': ('日本', 'JP'),
 'agent_Sint maartin agent list.html': ('圣马丁', 'SX'),
 'agent_Legal Assistance - U.S. Embassy in Slovenia.pdf': ('斯洛文尼亚', 'SI'),
 'agent_Legal Assistance - U.S. Embassy & Consulates in Türkiye.pdf': ('土耳其', 'TR'),
 'agent_Turkey agent list.html': ('土耳其', 'TR'),
 'agent_Uganda-attorney-list.pdf': ('乌干达', 'UG'),
 'agent_Ukraine agent list.html': ('乌克兰', 'UA'),
 'agent_乌克兰代理列表.xlsx': ('乌克兰', 'UA'),
 'agent_乌兹别克斯坦官网列表.xlsx': ('乌兹别克斯坦', 'UZ'),
 'agent_List of Attorneys - U.S. Embassy in Singapore.pdf': ('新加坡', 'SG'),
 'agent_新加坡代理.xls': ('新加坡', 'SG'),
 'agent_新加坡代理人A.pdf': ('新加坡', 'SG'),
 'agent_新加坡代理人B.pdf': ('新加坡', 'SG'),
 'agent_印度代理列表（部分）.pdf': ('印度', 'IN'),
 'agent_印度代理人.xlsx': ('印度', 'IN'),
 'agent_英国代理人-citma.txt': ('英国', 'GB'),
 'agent_Legal Assistance - U.S. Embassy & Consulate in Vietnam.pdf': ('越南', 'VN'),
 'agent_越南代理人.pdf': ('越南', 'VN'),
 'agent_越南代理人B.pdf': ('越南', 'VN')}
_COMPREHENSIVE = ['agent_2023-Jan-List-of-Lawyers.pdf',
 'agent_2024-January-Updated-List-of-Attorneys-Berlin.pdf',
 'agent_2025-Attorney-List.pdf',
 'agent_2025-January-Updated-List-of-Attorneys-Berlin.pdf',
 'agent_2025-September-Updated-List-of-Attorneys-Berlin.pdf',
 'agent_2026.00.10-List-of-Attorneys-Updated-March-2026-v3-1.pdf',
 'agent_450052245-List-of-Attorneys-US-embassy-pdf.pdf',
 'agent_ACS-Attorney-List-Jan-2021-updated-1.pdf',
 'agent_AM LIST-OF-ATTORNEYS.pdf',
 'agent_Attorney-list_Hyderabad-2025.pdf',
 'agent_Attorney-List-2024-JEDDAH-002.pdf',
 'agent_Attorney-Listing-2024.pdf',
 'agent_Attorney-List-July-2024.pdf',
 'agent_Attorney-List-July-2024-Latest-Feb-25.pdf',
 'agent_Attorney-List-Karachi.pdf',
 'agent_Attorney-List-May-2025.pdf',
 'agent_Attorney-List-NWD-CONS-District.pdf',
 'agent_Attorneys in Marseille and Southern France - U.S. Embassy & Consulates in France.pdf',
 'agent_Attorneys-List-MASTER-LIST-English-April-2022.pdf',
 'agent_Attorneys-List-MASTER-LIST-English-June-2022.pdf',
 'agent_Attorneys-List-MASTER-LIST-English-September-2024.pdf',
 'agent_Attorneys-list-updated-2025.pdf',
 'agent_Attorneys-Updated-Jan022026.pdf',
 'agent_EPO agent list 1.html',
 'agent_EPO agent list 2.html',
 'agent_ipcommunity.xlsx',
 'agent_ip-coster.xlsx',
 'agent_kpaa_ipridge_global_agents.xlsx',
 'agent_Legal Assistance - U.S. Embassy in Fiji, Kiribati, Nauru, and Tuvalu.pdf',
 'agent_list-attorneys-1.pdf',
 'agent_List-of-Attorney-for-U.S.-Consulate-Frankfurt-Nov.072025.pdf',
 'agent_List-of-Attorneys.pdf',
 'agent_List-of-Attorneys-2019.pdf',
 'agent_List-of-Attorneys-2024-Cairo-updated-1.pdf',
 'agent_List-of-Attorneys-and-Translators-in-Senegal-Spring-2023-Copy-1.pdf',
 'agent_LIST-OF-ATTORNEYS-April-2026.pdf',
 'agent_List-of-Attorneys-CIVIL-LAW-2024.pdf',
 'agent_LIST-OF-ATTORNEYS-in-alphabetical-order-Updated-Feb-4-2025-NEW.pdf',
 'agent_LIST-OF-ATTORNEYS-in-alphabetical-order-Updated-September-17-2025.pdf',
 'agent_LIST-OF-ATTORNEYS-January-2023.pdf',
 'agent_List-of-Attorneys-Merida-District.pdf',
 'agent_List-of-Attorneys-October-2022.pdf',
 'agent_List-of-Attorneys-ROC.pdf',
 'agent_List-of-Lawyers.pdf',
 'agent_Local_Lawyers_List_2025_Saudi_Arabia.pdf',
 'agent_Local-Attorneys-for-AbuDhabi-updated-Sept-2022.pdf',
 'agent_mofcom.xlsx',
 'agent_Sample-List-of-Local-Attorneys.pdf',
 'agent_updated-attorney-list-2021-2.pdf',
 'agent_代理库.xlsx',
 'agent_代理人汇总.xlsx',
 'agent_benrishi_navi_agents_dedup_main_with_contacts.xlsx',
 'agent_Calgary-Consular-District-List-of-Attorneys-April-2025.pdf',
 'agent_cnipa_trademark_agencies_all_lists_in_one.xlsx',
 'agent_Consular_District_of_Madrid-2021.pdf',
 'agent_cpata_public_register_agents.xlsx',
 'agent_cr.usembassy.gov_list-attorneys.pdf',
 'agent_Croatia agent list.pdf',
 'agent_Croatia patent agent list.pdf',
 'agent_EAPO agent list.html',
 'agent_Guernsey agent list.html',
 'agent_LawFirmList-update-on-Jan.-15-2025.pdf',
 'agent_Law-Firms-Kyiv-upd05072025.pdf',
 'agent_Lawyers-List-January-2024-Spanish-English.pdf',
 'agent_Lawyers-List-Update-May-2018.pdf',
 'agent_Legal Assistance - U.S. Consulate General Hong Kong & Macau.pdf',
 'agent_Legal Assistance - U.S. Embassy in Georgia.pdf',
 'agent_Legal Assistance - U.S. Embassy in Lithuania.pdf',
 'agent_Legal Assistance - U.S. Embassy Jerusalem.pdf',
 'agent_Legal Assistance\xa0_\xa0List of Lawyers in Sofia - U.S. Embassy in Bulgaria.pdf',
 'agent_Legal Assistance\xa0_\xa0List of Lawyers Outside of Sofia - U.S. Embassy in Bulgaria.pdf',
 'agent_List of Attorneys in Northern Taiwan - American Institute in Taiwan.pdf',
 'agent_martindale_us_trademark_lawyers.xlsx',
 'agent_Montreal-Attorney-List-Jan-2021-Updated.pdf']
_REGIONAL = {'agent_aripo agents 官网.xls': 'ARIPO',
 'agent_ARIPO Practitioners 官网.pdf': 'ARIPO',
 'agent_OAPI agent list.html': 'OAPI',
 'agent_europe_patent_attorneys_batch.xlsx': '欧盟'}

_SPECIFIC_NON_CN_BY_KEY = {
    normalize_source_name(name): (scope, country_code)
    for name, (scope, country_code) in _SPECIFIC_NON_CN.items()
}
_COMPREHENSIVE_KEYS = {normalize_source_name(name) for name in _COMPREHENSIVE}
_REGIONAL_BY_KEY = {
    normalize_source_name(name): scope
    for name, scope in _REGIONAL.items()
}


def _segment_for_name(normalized_name: str) -> str:
    if normalized_name == normalize_source_name("agent_美国申请人.csv"):
        return "DIRECT"
    if normalized_name.startswith("agent_"):
        return "AGENT"
    if normalized_name.startswith("【企查查】"):
        return "DIRECT"
    return ""


def lookup_source_catalog(source_name: str) -> SourceCatalogEntry | None:
    normalized = normalize_source_name(source_name)
    segment = _segment_for_name(normalized)
    if not segment:
        return None

    if normalized in _COMPREHENSIVE_KEYS:
        return SourceCatalogEntry(source_name, "综合", segment, "")

    regional_scope = _REGIONAL_BY_KEY.get(normalized)
    if regional_scope:
        return SourceCatalogEntry(source_name, regional_scope, segment, "")

    specific = _SPECIFIC_NON_CN_BY_KEY.get(normalized)
    if specific:
        scope, country_code = specific
        return SourceCatalogEntry(source_name, scope, segment, country_code)

    # The curated list marks every remaining non-agent QCC export as a CN direct source
    # and every remaining agent_* file as a CN agent source. Unknown future filenames are
    # intentionally not assigned CN unless they match these two reviewed families.
    if normalized.startswith("【企查查】"):
        return SourceCatalogEntry(source_name, "中国", "DIRECT", "CN")
    if normalized.startswith("agent_"):
        return SourceCatalogEntry(source_name, "中国", "AGENT", "CN")
    return None
