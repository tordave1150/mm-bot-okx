"""Build fresh socket-denied A0 evidence for the multi-session successor.

The command intentionally has no OKX adapter import path and blanks all known
credential environment variables in test subprocesses.  It verifies immutable
predecessors, runs the required suites with socket denial, records exact
exclusions, and writes the terminal A0 marker last.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from okx_demo_operational_failure_campaign import (
    EXPECTED_DECISIONS,
    SCENARIO_GROUPS,
)
from okx_demo_soak_failure_injection_offline import verify_predecessor
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_validation import canonical_sha256


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_multi_session_economic_soak"
FAILED_A2_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_multi_session_economic_soak" / "packages"
)
READY_STATUS = "OKX_DEMO_MULTI_SESSION_A0_OFFLINE_SUPPORT"
SUCCESSOR_SOURCE = "AGENTS_OKX_DEMO_MULTI_SESSION_ECONOMIC_SOAK_FAILURE_INJECTION.md"
SOAK_PACKAGE_ID = "soak-package-20260811T140223Z"
SOAK_RUN_ID = "soak-20260811T140223Z"
SOAK_HASHES = {
    "COMPLETED.json": (
        "e658a0aede428465d579434aa19a9e975f0564e6134b9577ee9f373a0eacd958"
    ),
    "SOAK_PACKAGE_COMPLETED.json": (
        "29e1743b978abaa5d7551e7515978f540029944e85961d2cf2ca0d488c2d1e56"
    ),
    "soak_run/completion_hashes.json": (
        "56e01765779d4eec5b75431f38ed52b7032da56552464b9a05d7a9c002c669c4"
    ),
    "soak_run/decision/decision.json": (
        "4fdd42ac418f597a62ee070483d534491aa3a4fba5f17f8612119e36b8bb215b"
    ),
}
FAILED_A2_PACKAGE_ID = "economic-package-20260811T164910Z"
FAILED_A2_CAMPAIGN_ID = "economic-campaign-20260811T164910Z"
FAILED_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260811T164910Z-s01-1a69fe577c"
)
FAILED_A2_HASHES = {
    "session/soak_run/FAILED.json": (
        "6dddb18bc6563eb0b825edafdde03d747289f792277a0ca3faea0ddeefe1e1ce"
    ),
    "session/soak_run/completion_hashes.json": (
        "85f1b1df6c9f42f300dc3df8b673ed2e2d002c7d1601eeb1acb595952dc9af76"
    ),
    "session/soak_run/audits/economics.json": (
        "cb79ba8cdd9f642dee3934a351615573fc3c8691620b172a0966691fe367dd67"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "1897e46f30771cd1842f0514a3cf703b221f0ec8223f999e1cbe49a0dd68fac3"
    ),
    "campaign/campaign_run/completion_hashes.json": (
        "fb9b876fcbb1e2457d213b647133d4132ecb19e4417b02cbc1a12328d7174427"
    ),
}
REPAIR_FAILED_A2_PACKAGE_ID = "economic-package-20260812T082847Z"
REPAIR_FAILED_A2_CAMPAIGN_ID = "economic-campaign-20260812T082847Z"
REPAIR_FAILED_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260812T082847Z-s01-7a3c1b97ef"
)
REPAIR_FAILED_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "f1675386b56165d687218ff273e503d8353b3403db4cf91d2e6613ce54317ed9"
    ),
    "session/soak_run/FAILED.json": (
        "071748e55d87b23e5aa93492c378f8bfb9ec7b4f1f344da17c2428dab4ba725f"
    ),
    "session/soak_run/completion_hashes.json": (
        "60b414693653f52eb3e61747dfda820c6bd9ea79d4c22ce1f6a54bc3ecf447bc"
    ),
    "session/soak_run/audits/economic_session_failure_evidence.json": (
        "989dc6ce9c8d3da1b04fdf3fc209fe8b01d24a291ad8e99ce832fd4b169b2bee"
    ),
    "session/soak_run/audits/economics.json": (
        "2b1a5fce2c35a4a029453d0ff079136c84d8b5dc1d653f69f6e5fd079f084506"
    ),
    "session/soak_run/audits/gateway_audit.json": (
        "19d318291a607b10fe6469597be2724db07fbd03f55c0847a9b1f7014aab1f04"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "f5faf4b43ced9398c455b57492a82f6cd457ae31708672d7d36dddf3c60fd67d"
    ),
    "campaign/campaign_run/completion_hashes.json": (
        "cc261e5e5dc7d94a64d2a6e98b5cf5172ecd617745b0df05171bb5d2eb11541f"
    ),
    "campaign/campaign_run/decision/campaign_decision.json": (
        "22ec2a011def555859b7c853cdcb718bbb9c82ec4d776814e39b7eb33ec36686"
    ),
    "campaign/campaign_run/attempted_sessions/slot-01.json": (
        "78bdae9aebdaeba2507328c70f4917f373f6d9fad2939ea1ff503e09adc798c0"
    ),
    "campaign/campaign_run/state/supervisor_state.json": (
        "5538a1dc4af3a235847a4be54a894728d6a65f11029ffd1f0aa8cf9fe94eaa56"
    ),
}
CLOCK_GATE_A2_PACKAGE_ID = "economic-package-20260812T103316Z"
CLOCK_GATE_A2_CAMPAIGN_ID = "economic-campaign-20260812T103316Z"
CLOCK_GATE_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260812T103316Z-s01-671d71cd43"
)
CLOCK_GATE_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "25e940b33fd75054bf69a50f3e6d58a490c2f40369a7ec9165885be348c51675"
    ),
    "session/soak_run/FAILED.json": (
        "7b4ff46d889753478af9647c16a7c825a6d6feb861a83d47e6c9a6a66465ce80"
    ),
    "session/soak_run/completion_hashes.json": (
        "af8fdc8adaeb2c533a947036945c9b2ba8c4aa3f5f07d7bbf11d42a917b0e5f6"
    ),
    "session/soak_run/audits/economic_session_failure_evidence.json": (
        "99add69ab2dd2f1f3f808cfc7d23f68b8a8fe52d99752a10948c4afb51125bc1"
    ),
    "session/soak_run/audits/economics.json": (
        "4b2051457a2499ccb6d842fda6a356a7d1de9503e4ea677d6fc970e5d34b3407"
    ),
    "session/soak_run/audits/gateway_audit.json": (
        "5c6f14f1bc6b8f1c31a660cffb0e39ba2fc90f5b7061466f7018dcd04e8f6fa0"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "ab87e4a3fcf98e6c611ef38177a4847176c2f0d1df7fc83266ea36c4fe36aba2"
    ),
    "campaign/campaign_run/completion_hashes.json": (
        "9984555b4675001d618acc2a94c4dd05668b0749a9bdf33c11c990f998ed9844"
    ),
    "campaign/campaign_run/decision/campaign_decision.json": (
        "a9d11f1250aece97cc000d8791ff970d1cfb87d0bd68e855c884672097308a83"
    ),
    "campaign/campaign_run/attempted_sessions/slot-01.json": (
        "75e7e2febff206a35ea5fbfe59fa8093cb21d1e883ef8f4db975b58bfc95f8fa"
    ),
    "campaign/campaign_run/state/supervisor_state.json": (
        "b06251432685de200cb76286f55d352d2d40cf15fcc887a488a67a23da15ac5c"
    ),
}
REGISTRY_PROJECTION_A2_PACKAGE_ID = "economic-package-20260812T144639Z"
REGISTRY_PROJECTION_A2_CAMPAIGN_ID = "economic-campaign-20260812T144639Z"
REGISTRY_PROJECTION_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260812T144639Z-s01-0728ed7a96"
)
REGISTRY_PROJECTION_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "9b152376229dee83a3e0fac48d28c905f84c4d0ff5ba7138383cb5517e78d6f0"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
        "bcdf28d80c133cafd4bf78b6a1d1aba270b220b2016962175f3e85b5606c4233"
    ),
    "campaign/campaign_run/state/supervisor_state.json": (
        "9e61784c5665106355b5ea41127a7e6761480f4d821bbc06a09718f5c679a257"
    ),
    "campaign/campaign_run/streams/supervisor_events.jsonl": (
        "743219404c1f12ac816a9ba08fe02d660972581cdf73065a7fe095494b5fcb02"
    ),
    "campaign/campaign_run/registry/campaign_registry.jsonl": (
        "d54c12ab340a36d4faf44474c6a53eca5bbfa4fd8909290248c81f3873e39a67"
    ),
    "session/COMPLETED.json": (
        "c80903232ad793c7bc3a42ad249472e914fe14e97f4cc9c3607fd7d3ab51f7f1"
    ),
    "session/soak_run/completion_hashes.json": (
        "70ae9040112c882598ff303ed254eb9abc3379d355ac41ce202c9e7994ddd299"
    ),
    "session/soak_run/audits/economic_session_evidence.json": (
        "c67b30057281acf2bd10de6c4fe7fc7c14dd8b2424481433317c421aa419436c"
    ),
    "session/soak_run/audits/economics.json": (
        "56b1f29b8b9b74c6c6bb35302cc996ab5b356125528725c732661a615c3dccf2"
    ),
    "session/soak_run/audits/gateway_audit.json": (
        "d39e4b711173cabd490cd482197577a8537aca55b9d3c8a348ae00b7b75447dd"
    ),
    "session/soak_run/terminal/account_snapshots.json": (
        "9c35a4677ef0ef38dc238930fa5604c2d598e843c8fd9b5b6cd5cf8dc9fa00b8"
    ),
    "session/campaign_authorization_consumed.json": (
        "b91ba60e674b63d659507d42b2bf0bf3dc6a322c6685baea5d4d33399911ac40"
    ),
}
CAUSAL_CLOCK_A2_PACKAGE_ID = "economic-package-20260812T153839Z"
CAUSAL_CLOCK_A2_CAMPAIGN_ID = "economic-campaign-20260812T153839Z"
CAUSAL_CLOCK_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260812T153839Z-s01-04d9bb3dce"
)
CAUSAL_CLOCK_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "15a085af59cf9158a031aeed8811cedd2fc8863af313f6bfd8965a6589caac3c"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
        "fe31db1a567968209819b9568a05161ff9940d1abc961645fdfd340f5a301d3e"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "dcd079e386b396f5d5fb79008b5a9a118a2d23c2e50d2bf14ef2468eb941e6b8"
    ),
    "campaign/campaign_run/completion_hashes.json": (
        "e639a78abd2fd5f81607961fbaf9cf7c879a207e7cbf6775e7a0554ed2a16572"
    ),
    "campaign/campaign_run/decision/campaign_decision.json": (
        "bb9e7177573245522c343f0324ab6d1b44618a0c370a5677a58874a26b4f508c"
    ),
    "campaign/campaign_run/attempted_sessions/slot-01.json": (
        "be47615b73f314946f548165dbd6fb7917f21a831e6a88e880a9d8efa6efa354"
    ),
    "campaign/campaign_run/state/supervisor_state.json": (
        "922aaece4fd8ad210e0bd0b523da6c32d785d859a8e26ce8549f3466c7f16be0"
    ),
    "campaign/campaign_run/streams/supervisor_events.jsonl": (
        "0630986548672233b429dd96e99179f876737c698d3138a516efad971f00e917"
    ),
    "campaign/campaign_run/registry/campaign_registry.jsonl": (
        "ac0be548cfc5eb2dc730078ac584a35c84d2c5600aa50e3377d472b6fd13b123"
    ),
    "session/soak_run/FAILED.json": (
        "66cdd098ee3fb476af2bf2aa970af3053c9529b5d88e3feb78590e11292b3740"
    ),
    "session/soak_run/completion_hashes.json": (
        "3db30e306f21b08a9bc4ac6584e248f6ee6eb7d171cec81787070d4a4a502324"
    ),
    "session/soak_run/audits/economic_session_failure_evidence.json": (
        "d303586c9b271549ef19a70c1d7ba2a4921343f95dbd3a12ce5dbcc1d944eb7b"
    ),
    "session/soak_run/audits/economics.json": (
        "8f13b0d1b08b6495f3f5e311fb4a61bcae4922d8798a3443b222905b73d98462"
    ),
    "session/soak_run/audits/gateway_audit.json": (
        "447d0af6ff0788e1fb27342d95bfb961050dcdab6f084f2a4b2c8135e350646e"
    ),
    "session/soak_run/terminal/account_snapshots.json": (
        "06af4754c54fe6f95bf96abd7d95db02e67d2ab6eb56eeb39d6ef24ba5de4d2e"
    ),
}
PRE_DISPATCH_CROSS_A2_PACKAGE_ID = "economic-package-20260813T133023Z"
PRE_DISPATCH_CROSS_A2_CAMPAIGN_ID = "economic-campaign-20260813T133023Z"
PRE_DISPATCH_CROSS_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260813T133023Z-s04-54411e2b57"
)
PRE_DISPATCH_CROSS_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "394778dda107a0db04a7cb8531fa33f3161f9bc4546d1e2c29331adf840b0364"
    ),
    "campaign/completion_hashes.json": (
        "141d19c143aa4be6fbfdd09fe734a13281d659656a3041e8d4d56c14d0ea0f25"
    ),
    "campaign/specification/campaign_package_spec.json": (
        "8f76d15401e14787967197f05b55624a67d9d6b7ef79db857cff6c71b4fd0997"
    ),
    "campaign/specification/session_package_audits.json": (
        "6fb13b3576e72393d7c071190d9105c0aaab0d0e3ac53c8e51cfb9584e725705"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
        "b9fad7e8787fe8c5e810f86dbb9c169785f653c24fe09b9ecb22b134a2a47b31"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "a571323d7913aa2d19acb63ec4ce74b288291fe2ac5138e0f3bf02f59d7dfd77"
    ),
    "campaign/campaign_run/completion_hashes.json": (
        "e165178010e0348598fa4fca06616df4d11dc49578ce9e9ceda514f0007beb75"
    ),
    "campaign/campaign_run/decision/campaign_decision.json": (
        "0de5ca97793050a652749693228d6bcdb3296edfe45379ab3134248aec4d376b"
    ),
    "campaign/campaign_run/attempted_sessions/slot-04.json": (
        "d6164e6b76251123b9c23a96d07ad03b2f896490e67d790c750ba15f882c5665"
    ),
    "campaign/campaign_run/streams/supervisor_events.jsonl": (
        "51a6b69645bf9ff66ee95d80695c3f4bea807bc73e78428b011149c36f41c087"
    ),
    "campaign/campaign_run/registry/campaign_registry.jsonl": (
        "3bf9147ce89d07fde053e404aa92a0ae4476fe08f412567ff1a298fefb560148"
    ),
    "session/soak_run/FAILED.json": (
        "deb37905c3a353fbbd0a40f00af8389d261a01710c83d5efce2dc01f4b2d2e65"
    ),
    "session/soak_run/UNRESOLVED_FAILURE.json": (
        "695d03d100636e115ab6dc74c64c282a6ad779e424bfbd5dce95c192378231bc"
    ),
    "session/soak_run/completion_hashes.json": (
        "14845630693d2516dabcc68cbc8030e756b7e502d2169c162dfe95d34b47f7b2"
    ),
    "session/soak_run/audits/economic_session_failure_evidence.json": (
        "30da3e197aeefd8446de37236ce7b4bb6542c4b44284683d53f4b2ed1453fc5c"
    ),
    "session/soak_run/audits/economics.json": (
        "6e3f26ed4ab43581ea76b4f385fb4f2b1fa5421ee43546b9b5d516bcdd6ce71d"
    ),
    "session/soak_run/audits/gateway_audit.json": (
        "cf761327520679366509cfa9aa08bbc19f9d8a6746f709e4ac046aa0a870067e"
    ),
    "session/soak_run/terminal/account_snapshots.json": (
        "e847fb05ddd5c39579c620fb6301bce18b2d95e6420f06edfaf746cb3bd0afbc"
    ),
}
INTERRUPTED_A2_PACKAGE_ID = "economic-package-20260813T144426Z"
INTERRUPTED_A2_CAMPAIGN_ID = "economic-campaign-20260813T144426Z"
INTERRUPTED_A2_SESSION_PACKAGE_ID = (
    "soak-package-20260813T144426Z-s08-dd72c9e660"
)
INTERRUPTED_A2_HASHES = {
    "campaign/A2_PACKAGE_COMPLETED.json": (
        "49a816dbb3130ce6c5232863cc51b59eb6ebb9dfa02df7e37183daf08d9097c0"
    ),
    "campaign/completion_hashes.json": (
        "65a0b25c6625343980a2cd3e4878195c8fcb3eb981a9c75b58a4b1ba203536b6"
    ),
    "campaign/specification/campaign_package_spec.json": (
        "66d0dc249867424f2c9c42fb6a5a7bfafcecae512e4bbba033f7912b626ad016"
    ),
    "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
        "19ec001f1fda7035675b5f89b6095a09ef16f5cbab4c57beec9df62dbca74621"
    ),
    "campaign/campaign_run/state/supervisor_state.json": (
        "29174c33111b57c29c63233e64d933816ce00a43e1dc915c46fd871e0c314e38"
    ),
    "campaign/campaign_run/streams/supervisor_events.jsonl": (
        "30d6dc9b3ac4b71cae41a058253bb19c85596155b5142a8ec943a6ef9d86583b"
    ),
    "campaign/campaign_run/registry/campaign_registry.jsonl": (
        "267dd07cbecddd5160497c6564f9526397496d29a7a9d64ff1bc3f2bb80bace5"
    ),
    "session/SOAK_PACKAGE_COMPLETED.json": (
        "aff2a534cd603573e6e76e0cbd02c00548fa71edcff711f920ec90527c21313d"
    ),
    "session/campaign_authorization.json": (
        "ac3a98421b1fee281bb041e592e87e9add98f8973e81587239807ba8cc71d043"
    ),
    "session/soak_run/SOAK_EXECUTION_ARMED.json": (
        "562f67165c5bdac5bc8cc708d6d58e3e6069984ff2f6fd1cd0b1ff1f427f2609"
    ),
    "session/soak_run/state/controller_state.jsonl": (
        "7c7e1c799f6fb09ae21e34d58ebc41bc00006ab805c87289cd3952eb008523f2"
    ),
    "session/soak_run/state/validation_state.json": (
        "3fedba2f0e25a5478c43ba7269afdd6bbd48b6e63ba04f6b6becf965d4c5a173"
    ),
    "session/soak_run/streams/events.jsonl": (
        "4d29818f03e2653e763ee3d63bbec50134dc0be1f405e69bc94f9cac359d3f17"
    ),
    "session/soak_run/state/lease.json": (
        "65e8e12af955b6a3d962445dfb34d8ac20eeae805b1ec38b042b951b30405c94"
    ),
    "session/soak_run/state/validation_state_journal.jsonl": (
        "ccc16794ac2df4f849360c944235cd5580fd7a1e1c54bdd494b659c155b7b514"
    ),
}
SOURCE_FILES = (
    "AGENTS.md",
    SUCCESSOR_SOURCE,
    "okx_demo_multi_session_campaign.py",
    "okx_demo_multi_session_prepare.py",
    "okx_demo_multi_session_supervisor.py",
    "okx_demo_operational_failure_campaign.py",
    "okx_demo_economic_session_controller.py",
    "okx_demo_economic_fill_engine.py",
    "okx_demo_adapter.py",
    "okx_demo_multi_session_a0_offline.py",
    "okx_demo_soak_executor.py",
    "okx_demo_soak_failure_injection.py",
    "okx_demo_soak_failure_injection_offline.py",
    "okx_demo_soak_prepare.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_validation.py",
    "okx_fill_restart_preflight.py",
    "okx_fill_restart_preflight_prepare.py",
    "okx_demo_runtime.py",
    "okx_offline_network_guard.py",
    "tests/test_okx_demo_multi_session_campaign.py",
    "tests/test_okx_demo_multi_session_prepare.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_operational_failure_campaign.py",
    "tests/test_okx_demo_economic_session_controller.py",
    "tests/test_okx_demo_economic_fill_engine.py",
    "tests/test_okx_demo_adapter.py",
    "tests/test_okx_demo_multi_session_a0_offline.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_soak_failure_injection.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_future_book_timestamp_repair.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_fill_restart_preflight_prepare.py",
    "tests/test_okx_a2_causal_economics_repair.py",
)
ROOT_EXCLUSIONS = (
    "tests/test_okx_fill_cursor_repair_offline.py",
    "tests/test_okx_fill_cursor_formal_prepare.py",
    "tests/test_okx_fill_restart_formal_prepare.py",
    "tests/test_okx_fill_restart_offline.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_production_readiness.py",
    "tests/test_okx_r2_warmup_audit_formal_prepare.py",
)
ROOT_DESELECT = (
    "tests/test_okx_r2_warmup_audit_counter_repair.py::"
    "test_successor_root_protocol_is_byte_identical_to_source_copy",
)
BACKTEST_EXCLUSIONS = (
    "backtest/tests/test_units_and_safety.py",
    "backtest/tests/test_robust_gates.py",
)
BACKTEST_DESELECT = (
    "backtest/tests/test_mm_v1_6_economics.py::test_root_agents_controls_v16_process",
)
TARGETED = (
    "tests/test_okx_demo_multi_session_campaign.py",
    "tests/test_okx_demo_multi_session_prepare.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_operational_failure_campaign.py",
    "tests/test_okx_demo_economic_session_controller.py",
    "tests/test_okx_demo_economic_fill_engine.py",
    "tests/test_okx_demo_adapter.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_soak_failure_injection.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_future_book_timestamp_repair.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_fill_restart_preflight_prepare.py",
    "tests/test_okx_a2_causal_economics_repair.py",
    *(f"--deselect={node}" for node in ROOT_DESELECT),
)


class A0OfflineError(RuntimeError):
    pass


class SocketGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create = socket.create_connection

    def __enter__(self) -> "SocketGuard":
        guard = self

        def blocked(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise A0OfflineError("network prohibited during A0 offline evidence")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            blocked(instance, address)
            return 1

        def blocked_create(address: object, *args: object, **kwargs: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise A0OfflineError("network prohibited during A0 offline evidence")

        socket.socket.connect = blocked  # type: ignore[method-assign]
        socket.socket.connect_ex = blocked_ex  # type: ignore[method-assign]
        socket.create_connection = blocked_create  # type: ignore[assignment]
        return self

    def __exit__(self, *args: object) -> None:
        socket.socket.connect = self._connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._create


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise A0OfflineError(f"JSON cannot be loaded: {path}") from exc
    if not isinstance(value, dict):
        raise A0OfflineError(f"JSON object expected: {path}")
    return value


def _last_jsonl(path: Path) -> dict[str, Any]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception as exc:
        raise A0OfflineError(f"JSONL cannot be loaded: {path}") from exc
    if not rows or not isinstance(rows[-1], dict):
        raise A0OfflineError(f"JSONL object expected: {path}")
    return rows[-1]


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise A0OfflineError(f"source is missing: {relative}")
        hashes[relative] = _sha256(path)
    if hashes["AGENTS.md"] != hashes[SUCCESSOR_SOURCE]:
        raise A0OfflineError("root AGENTS is not byte-identical to successor source")
    return dict(sorted(hashes.items()))


def verify_soak_predecessor(root: Path) -> dict[str, object]:
    package = root / "artifacts" / "okx_demo_soak_validation" / SOAK_PACKAGE_ID
    failures = [
        relative for relative, expected in SOAK_HASHES.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    terminal = _json(package / "COMPLETED.json")
    economics = _json(package / "soak_run/audits/economics.json")
    decision = _json(package / "soak_run/decision/decision.json")
    snapshots = _json(package / "soak_run/terminal/account_snapshots.json")
    marker_count = sum(
        path.name == "SOAK_EXECUTION_ARMED.json"
        for path in package.rglob("SOAK_EXECUTION_ARMED.json")
    )
    if any((
        failures,
        terminal.get("status") != "OKX_DEMO_BOUNDED_SOAK_SUPPORT",
        terminal.get("run_id") != SOAK_RUN_ID,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        terminal.get("two_flat_empty_snapshots") is not True,
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        economics.get("normal_bid_fills") != 1,
        economics.get("normal_ask_fills") != 0,
        economics.get("normal_fifo_round_trips") != 0,
        economics.get("normal_net_realized_pnl_usdt") != "-0.1283324",
        economics.get("aggregate_net_realized_pnl_usdt") != "-0.3791984",
        decision.get("decision") != "TERMINAL_COMPLETE",
        marker_count != 1,
        not isinstance(snapshots.get("first"), dict),
        not isinstance(snapshots.get("second"), dict),
    )):
        raise A0OfflineError(
            "bounded-soak predecessor changed: " + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "package_id": SOAK_PACKAGE_ID,
        "run_id": SOAK_RUN_ID,
        "fixed_hashes": SOAK_HASHES,
        "execution_marker_count": marker_count,
        "final_position_btc": "0",
        "final_open_orders": 0,
        "two_flat_empty_snapshots": True,
        "normal_bid_fills": 1,
        "normal_ask_fills": 0,
        "normal_fifo_round_trips": 0,
        "normal_net_pnl_usdt": "-0.1283324",
        "aggregate_net_pnl_usdt": "-0.3791984",
        "operational_safety_evidence_only": True,
        "economic_promotion_evidence": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "rerun_allowed": False,
    }


def verify_failed_a2_predecessor(root: Path) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / FAILED_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / FAILED_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "session/soak_run/FAILED.json": session / "soak_run/FAILED.json",
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
        ),
        "campaign/campaign_run/completion_hashes.json": (
            campaign / "campaign_run/completion_hashes.json"
        ),
    }
    failures = [
        name for name, expected in FAILED_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    failed = _json(mapped["session/soak_run/FAILED.json"])
    economics = _json(mapped["session/soak_run/audits/economics.json"])
    terminal = _json(
        mapped["campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json"]
    )
    state = _json(campaign / "campaign_run/state/supervisor_state.json")
    session_markers = sorted(
        path.parent.parent.name
        for path in (
            root / "artifacts" / "okx_demo_soak_validation"
        ).glob("soak-package-20260811T164910Z-*/soak_run/SOAK_EXECUTION_ARMED.json")
    )
    if any((
        failures,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("normal_creates") != 60,
        failed.get("terminal_account_flat_empty") is not True,
        failed.get("reason")
        != "CampaignError:causal re-entry evidence is incomplete",
        economics.get("normal_bid_fills") != 6,
        economics.get("normal_ask_fills") != 13,
        economics.get("normal_fifo_round_trips") != 16,
        economics.get("normal_net_realized_pnl_usdt") != "-1.848902742",
        economics.get("special_net_realized_pnl_usdt") != "-0.12004687",
        economics.get("aggregate_net_realized_pnl_usdt") != "-1.968949612",
        len(economics.get("pending_causal_fills") or []) != 1,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_completed") != 0,
        state.get("active_slot") != 1,
        state.get("completed_slots") != [],
        len(session_markers) != 1,
        session_markers
        and session_markers[0] != FAILED_A2_SESSION_PACKAGE_ID,
    )):
        raise A0OfflineError(
            "failed A2 predecessor changed: " + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": FAILED_A2_PACKAGE_ID,
        "campaign_id": FAILED_A2_CAMPAIGN_ID,
        "session_package_id": FAILED_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": FAILED_A2_HASHES,
        "session_marker_count": len(session_markers),
        "session_two_never_started": True,
        "normal_creates": 60,
        "normal_bid_fills": 6,
        "normal_ask_fills": 13,
        "legacy_fragment_round_trip_counter": 16,
        "pending_causal_fill_count": 1,
        "normal_net_pnl_usdt": "-1.848902742",
        "special_net_pnl_usdt": "-0.12004687",
        "aggregate_net_pnl_usdt": "-1.968949612",
        "historical_campaign_omitted_attempted_session": True,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_repair_failed_a2_predecessor(root: Path) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / REPAIR_FAILED_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / REPAIR_FAILED_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": (
            campaign / "A2_PACKAGE_COMPLETED.json"
        ),
        "session/soak_run/FAILED.json": session / "soak_run/FAILED.json",
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economic_session_failure_evidence.json": (
            session / "soak_run/audits/economic_session_failure_evidence.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "session/soak_run/audits/gateway_audit.json": (
            session / "soak_run/audits/gateway_audit.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
        ),
        "campaign/campaign_run/completion_hashes.json": (
            campaign / "campaign_run/completion_hashes.json"
        ),
        "campaign/campaign_run/decision/campaign_decision.json": (
            campaign / "campaign_run/decision/campaign_decision.json"
        ),
        "campaign/campaign_run/attempted_sessions/slot-01.json": (
            campaign / "campaign_run/attempted_sessions/slot-01.json"
        ),
        "campaign/campaign_run/state/supervisor_state.json": (
            campaign / "campaign_run/state/supervisor_state.json"
        ),
    }
    failures = [
        name for name, expected in REPAIR_FAILED_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    failed = _json(mapped["session/soak_run/FAILED.json"])
    economics = _json(mapped["session/soak_run/audits/economics.json"])
    gateway = _json(mapped["session/soak_run/audits/gateway_audit.json"])
    terminal = _json(
        mapped["campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json"]
    )
    decision = _json(
        mapped["campaign/campaign_run/decision/campaign_decision.json"]
    )
    state = _json(mapped["campaign/campaign_run/state/supervisor_state.json"])
    attempted = dict(decision.get("attempted_aggregate") or {})
    session_markers = sorted(
        path.parent.parent.name
        for path in (
            root / "artifacts" / "okx_demo_soak_validation"
        ).glob(
            "soak-package-20260812T082847Z-*/soak_run/"
            "SOAK_EXECUTION_ARMED.json"
        )
    )
    if any((
        failures,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason") != "ValidationSafetyError:ORDER_ACK_REJECTED",
        failed.get("normal_creates") != 17,
        failed.get("normal_cancels") != 16,
        failed.get("normal_bid_fills") != 1,
        failed.get("normal_ask_fills") != 1,
        failed.get("normal_fifo_round_trips") != 1,
        failed.get("normal_net_pnl_usdt") != "0.5110036",
        failed.get("terminal_account_flat_empty") is not True,
        gateway.get("mutation_method_counts", {}).get("create_order") != 18,
        gateway.get("mutation_method_counts", {}).get("cancel_order") != 16,
        economics.get("normal_gross_realized_pnl_usdt") != "0.7660",
        economics.get("normal_fees_usdt") != "0.2549964",
        economics.get("normal_net_realized_pnl_usdt") != "0.5110036",
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 1,
        terminal.get("sessions_completed") != 0,
        terminal.get("sessions_failed") != 1,
        terminal.get("terminal_account_authoritative") is not True,
        state.get("active_slot") is not None,
        state.get("completed_slots") != [],
        state.get("failed_slots") != [1],
        attempted.get("normal_creates") != 17,
        attempted.get("normal_gross_pnl_usdt") != "0",
        attempted.get("normal_fees_usdt") != "0",
        attempted.get("normal_net_pnl_usdt") != "0.5110036",
        len(session_markers) != 1,
        session_markers
        and session_markers[0] != REPAIR_FAILED_A2_SESSION_PACKAGE_ID,
    )):
        raise A0OfflineError(
            "repair failed A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": REPAIR_FAILED_A2_PACKAGE_ID,
        "campaign_id": REPAIR_FAILED_A2_CAMPAIGN_ID,
        "session_package_id": REPAIR_FAILED_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": REPAIR_FAILED_A2_HASHES,
        "session_marker_count": len(session_markers),
        "session_two_never_started": True,
        "failure_reason": failed["reason"],
        "reported_normal_creates": 17,
        "authoritative_normal_create_dispatches": 18,
        "normal_cancels": 16,
        "normal_bid_fills": 1,
        "normal_ask_fills": 1,
        "normal_fifo_round_trips": 1,
        "normal_gross_pnl_usdt": "0.7660",
        "normal_fees_usdt": "0.2549964",
        "normal_net_pnl_usdt": "0.5110036",
        "historical_create_counter_mismatch": True,
        "historical_failed_accounting_components_omitted": True,
        "terminal_account_authoritative": True,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_clock_gate_failed_a2_predecessor(root: Path) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / CLOCK_GATE_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / CLOCK_GATE_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": (
            campaign / "A2_PACKAGE_COMPLETED.json"
        ),
        "session/soak_run/FAILED.json": session / "soak_run/FAILED.json",
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economic_session_failure_evidence.json": (
            session / "soak_run/audits/economic_session_failure_evidence.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "session/soak_run/audits/gateway_audit.json": (
            session / "soak_run/audits/gateway_audit.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
        ),
        "campaign/campaign_run/completion_hashes.json": (
            campaign / "campaign_run/completion_hashes.json"
        ),
        "campaign/campaign_run/decision/campaign_decision.json": (
            campaign / "campaign_run/decision/campaign_decision.json"
        ),
        "campaign/campaign_run/attempted_sessions/slot-01.json": (
            campaign / "campaign_run/attempted_sessions/slot-01.json"
        ),
        "campaign/campaign_run/state/supervisor_state.json": (
            campaign / "campaign_run/state/supervisor_state.json"
        ),
    }
    failures = [
        name for name, expected in CLOCK_GATE_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    failed = _json(mapped["session/soak_run/FAILED.json"])
    evidence = _json(
        mapped[
            "session/soak_run/audits/economic_session_failure_evidence.json"
        ]
    )
    gateway = _json(mapped["session/soak_run/audits/gateway_audit.json"])
    snapshots = _json(session / "soak_run/terminal/account_snapshots.json")
    terminal = _json(
        mapped["campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json"]
    )
    decision = _json(
        mapped["campaign/campaign_run/decision/campaign_decision.json"]
    )
    state = _json(mapped["campaign/campaign_run/state/supervisor_state.json"])
    attempted = dict(decision.get("attempted_aggregate") or {})
    first = dict(snapshots.get("first") or {})
    second = dict(snapshots.get("second") or {})
    session_markers = sorted(
        path.parent.parent.name
        for path in (
            root / "artifacts" / "okx_demo_soak_validation"
        ).glob(
            "soak-package-20260812T103316Z-*/soak_run/"
            "SOAK_EXECUTION_ARMED.json"
        )
    )
    zero_create_fields = (
        "normal_creates",
        "normal_create_dispatches",
        "normal_create_acknowledgements",
        "normal_create_rejections",
        "normal_create_unresolved",
        "normal_cancels",
        "normal_bid_fills",
        "normal_ask_fills",
        "normal_fifo_round_trips",
    )
    if any((
        failures,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason") != "SoakExecutionError:account clock skew exceeds budget",
        any(int(failed.get(name, -1)) != 0 for name in zero_create_fields),
        failed.get("mutation_retries") != 0,
        failed.get("terminal_account_flat_empty") is not True,
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_snapshots") != 2,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        evidence.get("evidence_kind") != "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
        gateway.get("mutation_call_count") != 0,
        gateway.get("normal_create_dispatches") != 0,
        gateway.get("flatten_dispatches") != 0,
        first.get("clock_skew_ms") != 2015,
        second.get("clock_skew_ms") != 2013,
        first.get("position_btc") != "0",
        second.get("position_btc") != "0",
        first.get("open_orders") != 0,
        second.get("open_orders") != 0,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 1,
        terminal.get("sessions_completed") != 0,
        terminal.get("sessions_failed") != 1,
        terminal.get("terminal_account_authoritative") is not True,
        state.get("active_slot") is not None,
        state.get("completed_slots") != [],
        state.get("failed_slots") != [1],
        attempted.get("normal_create_dispatches") != 0,
        attempted.get("create_counter_reconciles") is not True,
        attempted.get("economic_attribution_reconciles") is not True,
        len(session_markers) != 1,
        session_markers
        and session_markers[0] != CLOCK_GATE_A2_SESSION_PACKAGE_ID,
    )):
        raise A0OfflineError(
            "clock-gate failed A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": CLOCK_GATE_A2_PACKAGE_ID,
        "campaign_id": CLOCK_GATE_A2_CAMPAIGN_ID,
        "session_package_id": CLOCK_GATE_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": CLOCK_GATE_A2_HASHES,
        "session_marker_count": len(session_markers),
        "session_two_never_started": True,
        "failure_reason": failed["reason"],
        "clock_skew_ms": [2015, 2013],
        "maximum_clock_skew_ms": 1500,
        "pre_mutation_failure": True,
        "normal_create_dispatches": 0,
        "orders_submitted": 0,
        "orders_amended": 0,
        "orders_cancelled": 0,
        "terminal_account_authoritative": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_registry_projection_failed_a2_predecessor(
    root: Path,
) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / REGISTRY_PROJECTION_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / REGISTRY_PROJECTION_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": campaign / "A2_PACKAGE_COMPLETED.json",
        "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_ARMED.json"
        ),
        "campaign/campaign_run/state/supervisor_state.json": (
            campaign / "campaign_run/state/supervisor_state.json"
        ),
        "campaign/campaign_run/streams/supervisor_events.jsonl": (
            campaign / "campaign_run/streams/supervisor_events.jsonl"
        ),
        "campaign/campaign_run/registry/campaign_registry.jsonl": (
            campaign / "campaign_run/registry/campaign_registry.jsonl"
        ),
        "session/COMPLETED.json": session / "COMPLETED.json",
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economic_session_evidence.json": (
            session / "soak_run/audits/economic_session_evidence.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "session/soak_run/audits/gateway_audit.json": (
            session / "soak_run/audits/gateway_audit.json"
        ),
        "session/soak_run/terminal/account_snapshots.json": (
            session / "soak_run/terminal/account_snapshots.json"
        ),
        "session/campaign_authorization_consumed.json": (
            session / "campaign_authorization_consumed.json"
        ),
    }
    failures = [
        name for name, expected in REGISTRY_PROJECTION_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    child_terminal = _json(mapped["session/COMPLETED.json"])
    child_evidence = _json(
        mapped["session/soak_run/audits/economic_session_evidence.json"]
    )
    economics = _json(mapped["session/soak_run/audits/economics.json"])
    gateway = _json(mapped["session/soak_run/audits/gateway_audit.json"])
    snapshots = _json(mapped["session/soak_run/terminal/account_snapshots.json"])
    state = _json(mapped["campaign/campaign_run/state/supervisor_state.json"])
    events = [
        json.loads(line)
        for line in mapped[
            "campaign/campaign_run/streams/supervisor_events.jsonl"
        ].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records = [
        json.loads(line)
        for line in mapped[
            "campaign/campaign_run/registry/campaign_registry.jsonl"
        ].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    accepted = dict(records[1]["payload"]["session"]) if len(records) == 2 else {}
    original = dict(child_evidence)
    original_seal = str(original.pop("evidence_sha256", ""))
    accepted_seal = str(accepted.pop("evidence_sha256", ""))
    lost_fields = {
        "normal_create_dispatches",
        "normal_create_acknowledgements",
        "normal_create_rejections",
        "normal_create_unresolved",
        "reconciles",
    }
    expected_projection = {
        key: value for key, value in original.items() if key not in lost_fields
    }
    first = dict(snapshots.get("first") or {})
    second = dict(snapshots.get("second") or {})
    slot_two = (
        root / "artifacts" / "okx_demo_soak_validation"
        / "soak-package-20260812T144639Z-s02-bbeb43ac65"
    )
    if any((
        failures,
        child_terminal.get("status") != "OKX_DEMO_ECONOMIC_SESSION_SUPPORT",
        child_terminal.get("final_position_btc") != "0",
        child_terminal.get("final_open_orders") != 0,
        child_terminal.get("two_flat_empty_snapshots") is not True,
        child_terminal.get("controller_engine_gateway_reconciled") is not True,
        child_terminal.get("normal_create_dispatches") != 38,
        child_terminal.get("normal_create_acknowledgements") != 38,
        child_terminal.get("normal_create_rejections") != 0,
        child_terminal.get("normal_create_unresolved") != 0,
        child_terminal.get("mutation_retries") != 0,
        child_terminal.get("live_endpoint_attempts") != 0,
        child_terminal.get("live_orders") != 0,
        gateway.get("normal_create_dispatches") != 38,
        gateway.get("flatten_dispatches") != 1,
        gateway.get("live_endpoint_attempts") != 0,
        gateway.get("live_orders") != 0,
        economics.get("normal_bid_fills") != 1,
        economics.get("normal_ask_fills") != 5,
        economics.get("normal_fifo_round_trips") != 1,
        economics.get("normal_net_realized_pnl_usdt") != "1.693925200",
        economics.get("special_net_realized_pnl_usdt") != "-1.6541625",
        economics.get("aggregate_net_realized_pnl_usdt") != "0.039762700",
        first.get("position_btc") != "0",
        second.get("position_btc") != "0",
        first.get("open_orders") != 0,
        second.get("open_orders") != 0,
        state.get("active_slot") is not None,
        state.get("completed_slots") != [1],
        state.get("failed_slots") != [],
        state.get("terminal_decision") is not None,
        state.get("terminal_account_authoritative") is not True,
        state.get("terminal_final_position_btc") != "0",
        state.get("terminal_final_open_orders") != 0,
        len(events) != 4,
        events and events[-1].get("event") != "SESSION_ACCEPTED",
        len(records) != 2,
        original_seal != canonical_sha256(original),
        accepted_seal != original_seal,
        accepted_seal == canonical_sha256(accepted),
        accepted != expected_projection,
        any(field in accepted for field in lost_fields),
        (campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json").exists(),
        (slot_two / "campaign_authorization.json").exists(),
        (slot_two / "soak_run/SOAK_EXECUTION_ARMED.json").exists(),
    )):
        raise A0OfflineError(
            "registry-projection failed A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": REGISTRY_PROJECTION_A2_PACKAGE_ID,
        "campaign_id": REGISTRY_PROJECTION_A2_CAMPAIGN_ID,
        "session_package_id": REGISTRY_PROJECTION_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": REGISTRY_PROJECTION_A2_HASHES,
        "failure_reason": "CampaignError:session evidence seal mismatch",
        "failure_boundary": "before_slot_2_authorization",
        "source_evidence_seal_valid": True,
        "registry_projection_seal_valid": False,
        "lost_create_counter_fields": sorted(lost_fields),
        "session_two_never_authorized": True,
        "session_two_never_started": True,
        "normal_create_dispatches": 38,
        "normal_create_acknowledgements": 38,
        "normal_create_unresolved": 0,
        "mutation_retries": 0,
        "normal_bid_fills": 1,
        "normal_ask_fills": 5,
        "normal_fifo_round_trips": 1,
        "normal_net_pnl_usdt": "1.693925200",
        "special_net_pnl_usdt": "-1.6541625",
        "aggregate_net_pnl_usdt": "0.039762700",
        "terminal_account_authoritative": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "two_flat_empty_snapshots": True,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_causal_clock_failed_a2_predecessor(
    root: Path,
) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / CAUSAL_CLOCK_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / CAUSAL_CLOCK_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": campaign / "A2_PACKAGE_COMPLETED.json",
        "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_ARMED.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
        ),
        "campaign/campaign_run/completion_hashes.json": (
            campaign / "campaign_run/completion_hashes.json"
        ),
        "campaign/campaign_run/decision/campaign_decision.json": (
            campaign / "campaign_run/decision/campaign_decision.json"
        ),
        "campaign/campaign_run/attempted_sessions/slot-01.json": (
            campaign / "campaign_run/attempted_sessions/slot-01.json"
        ),
        "campaign/campaign_run/state/supervisor_state.json": (
            campaign / "campaign_run/state/supervisor_state.json"
        ),
        "campaign/campaign_run/streams/supervisor_events.jsonl": (
            campaign / "campaign_run/streams/supervisor_events.jsonl"
        ),
        "campaign/campaign_run/registry/campaign_registry.jsonl": (
            campaign / "campaign_run/registry/campaign_registry.jsonl"
        ),
        "session/soak_run/FAILED.json": session / "soak_run/FAILED.json",
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economic_session_failure_evidence.json": (
            session / "soak_run/audits/economic_session_failure_evidence.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "session/soak_run/audits/gateway_audit.json": (
            session / "soak_run/audits/gateway_audit.json"
        ),
        "session/soak_run/terminal/account_snapshots.json": (
            session / "soak_run/terminal/account_snapshots.json"
        ),
    }
    failures = [
        name for name, expected in CAUSAL_CLOCK_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    failed = _json(mapped["session/soak_run/FAILED.json"])
    evidence = _json(
        mapped["session/soak_run/audits/economic_session_failure_evidence.json"]
    )
    economics = _json(mapped["session/soak_run/audits/economics.json"])
    gateway = _json(mapped["session/soak_run/audits/gateway_audit.json"])
    snapshots = _json(mapped["session/soak_run/terminal/account_snapshots.json"])
    terminal = _json(mapped["campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json"])
    decision = _json(mapped["campaign/campaign_run/decision/campaign_decision.json"])
    state = _json(mapped["campaign/campaign_run/state/supervisor_state.json"])
    pending = list(economics.get("pending_causal_fills") or [])
    pending_row = dict(pending[0]) if len(pending) == 1 else {}
    attempted = dict(decision.get("attempted_aggregate") or {})
    first = dict(snapshots.get("first") or {})
    second = dict(snapshots.get("second") or {})
    slot_two = (
        root / "artifacts" / "okx_demo_soak_validation"
        / "soak-package-20260812T153839Z-s02-d2f23f68d3"
    )
    if any((
        failures,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason") != "CampaignError:inventory defense precedes fill",
        failed.get("normal_create_dispatches") != 2,
        failed.get("normal_create_acknowledgements") != 2,
        failed.get("normal_create_rejections") != 0,
        failed.get("normal_create_unresolved") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("normal_bid_fills") != 0,
        failed.get("normal_ask_fills") != 1,
        failed.get("terminal_account_authoritative") is not True,
        failed.get("two_flat_empty_snapshots") is not True,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        evidence.get("evidence_kind") != "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
        evidence.get("pending_causal_fill_count") != 1,
        len(pending) != 1,
        pending_row.get("defense_timestamp_ms") != 0,
        int(pending_row.get("fill_timestamp_ms", 0)) <= 0,
        gateway.get("normal_create_dispatches") != 2,
        gateway.get("flatten_dispatches") != 1,
        gateway.get("live_endpoint_attempts") != 0,
        first.get("clock_skew_ms") != 1307,
        second.get("clock_skew_ms") != 1311,
        first.get("position_btc") != "0",
        second.get("position_btc") != "0",
        first.get("open_orders") != 0,
        second.get("open_orders") != 0,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 1,
        terminal.get("sessions_completed") != 0,
        terminal.get("sessions_failed") != 1,
        terminal.get("terminal_account_authoritative") is not True,
        state.get("active_slot") is not None,
        state.get("completed_slots") != [],
        state.get("failed_slots") != [1],
        state.get("terminal_decision") != "NOT_READY",
        attempted.get("normal_create_dispatches") != 2,
        attempted.get("normal_create_acknowledgements") != 2,
        attempted.get("create_counter_reconciles") is not True,
        attempted.get("economic_attribution_reconciles") is not True,
        (slot_two / "campaign_authorization.json").exists(),
        (slot_two / "soak_run/SOAK_EXECUTION_ARMED.json").exists(),
    )):
        raise A0OfflineError(
            "causal-clock failed A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": CAUSAL_CLOCK_A2_PACKAGE_ID,
        "campaign_id": CAUSAL_CLOCK_A2_CAMPAIGN_ID,
        "session_package_id": CAUSAL_CLOCK_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": CAUSAL_CLOCK_A2_HASHES,
        "failure_reason": failed["reason"],
        "failure_boundary": "slot_1_before_slot_2_authorization",
        "future_fill_within_clock_skew_budget": True,
        "clock_skew_ms": [1307, 1311],
        "maximum_clock_skew_ms": 1500,
        "historical_defense_timestamp_ms": 0,
        "fill_timestamp_ms": pending_row["fill_timestamp_ms"],
        "session_two_never_authorized": True,
        "session_two_never_started": True,
        "normal_create_dispatches": 2,
        "normal_create_acknowledgements": 2,
        "normal_create_unresolved": 0,
        "mutation_retries": 0,
        "terminal_account_authoritative": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "two_flat_empty_snapshots": True,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_pre_dispatch_cross_failed_a2_predecessor(
    root: Path,
) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / PRE_DISPATCH_CROSS_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / PRE_DISPATCH_CROSS_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": campaign / "A2_PACKAGE_COMPLETED.json",
        "campaign/completion_hashes.json": campaign / "completion_hashes.json",
        "campaign/specification/campaign_package_spec.json": (
            campaign / "specification/campaign_package_spec.json"
        ),
        "campaign/specification/session_package_audits.json": (
            campaign / "specification/session_package_audits.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_ARMED.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
        ),
        "campaign/campaign_run/completion_hashes.json": (
            campaign / "campaign_run/completion_hashes.json"
        ),
        "campaign/campaign_run/decision/campaign_decision.json": (
            campaign / "campaign_run/decision/campaign_decision.json"
        ),
        "campaign/campaign_run/attempted_sessions/slot-04.json": (
            campaign / "campaign_run/attempted_sessions/slot-04.json"
        ),
        "campaign/campaign_run/streams/supervisor_events.jsonl": (
            campaign / "campaign_run/streams/supervisor_events.jsonl"
        ),
        "campaign/campaign_run/registry/campaign_registry.jsonl": (
            campaign / "campaign_run/registry/campaign_registry.jsonl"
        ),
        "session/soak_run/FAILED.json": session / "soak_run/FAILED.json",
        "session/soak_run/UNRESOLVED_FAILURE.json": (
            session / "soak_run/UNRESOLVED_FAILURE.json"
        ),
        "session/soak_run/completion_hashes.json": (
            session / "soak_run/completion_hashes.json"
        ),
        "session/soak_run/audits/economic_session_failure_evidence.json": (
            session / "soak_run/audits/economic_session_failure_evidence.json"
        ),
        "session/soak_run/audits/economics.json": (
            session / "soak_run/audits/economics.json"
        ),
        "session/soak_run/audits/gateway_audit.json": (
            session / "soak_run/audits/gateway_audit.json"
        ),
        "session/soak_run/terminal/account_snapshots.json": (
            session / "soak_run/terminal/account_snapshots.json"
        ),
    }
    failures = [
        name for name, expected in PRE_DISPATCH_CROSS_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    spec = _json(mapped["campaign/specification/campaign_package_spec.json"])
    terminal = _json(mapped["campaign/campaign_run/A2_CAMPAIGN_COMPLETED.json"])
    decision = _json(mapped["campaign/campaign_run/decision/campaign_decision.json"])
    attempted = dict(decision.get("attempted_aggregate") or {})
    completed = dict(decision.get("completed_aggregate") or {})
    slot_four = _json(
        mapped["campaign/campaign_run/attempted_sessions/slot-04.json"]
    )
    failed = _json(mapped["session/soak_run/FAILED.json"])
    evidence = _json(
        mapped["session/soak_run/audits/economic_session_failure_evidence.json"]
    )
    gateway = _json(mapped["session/soak_run/audits/gateway_audit.json"])
    snapshots = _json(mapped["session/soak_run/terminal/account_snapshots.json"])
    first = dict(snapshots.get("first") or {})
    second = dict(snapshots.get("second") or {})
    later_started: list[int] = []
    for row in list(spec.get("session_slots") or [])[4:]:
        child = (
            root / "artifacts" / "okx_demo_soak_validation"
            / str(row.get("package_id") or "")
        )
        if (
            (child / "campaign_authorization.json").exists()
            or (child / "soak_run/SOAK_EXECUTION_ARMED.json").exists()
        ):
            later_started.append(int(row.get("slot") or 0))
    if any((
        failures,
        spec.get("campaign_id") != PRE_DISPATCH_CROSS_A2_CAMPAIGN_ID,
        len(list(spec.get("session_slots") or [])) != 12,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason")
        != "FormalGatewayError:formal post-only order would cross current BBO",
        failed.get("normal_create_dispatches") != 23,
        failed.get("normal_create_acknowledgements") != 23,
        failed.get("normal_create_rejections") != 0,
        failed.get("normal_create_unresolved") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("normal_bid_fills") != 1,
        failed.get("normal_ask_fills") != 1,
        failed.get("normal_fifo_round_trips") != 1,
        failed.get("normal_net_pnl_usdt") != "0.0750752",
        failed.get("special_fill_count") != 0,
        failed.get("terminal_account_authoritative") is not True,
        failed.get("two_flat_empty_snapshots") is not True,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        evidence.get("evidence_kind") != "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
        evidence.get("pending_causal_fill_count") != 0,
        gateway.get("normal_create_dispatches") != 23,
        gateway.get("flatten_dispatches") != 0,
        gateway.get("live_endpoint_attempts") != 0,
        first.get("clock_skew_ms") != 1359,
        second.get("clock_skew_ms") != 1361,
        first.get("position_btc") != "0",
        second.get("position_btc") != "0",
        first.get("open_orders") != 0,
        second.get("open_orders") != 0,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 4,
        terminal.get("sessions_completed") != 3,
        terminal.get("sessions_failed") != 1,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        decision.get("decision") != "NOT_READY",
        attempted.get("normal_create_dispatches") != 168,
        attempted.get("normal_create_acknowledgements") != 168,
        attempted.get("normal_create_unresolved") != 0,
        attempted.get("create_counter_reconciles") is not True,
        attempted.get("economic_attribution_reconciles") is not False,
        attempted.get("normal_gross_pnl_usdt") != "0.5960",
        attempted.get("normal_fees_usdt") != "0.5100332",
        attempted.get("normal_net_pnl_usdt") != "0.0859668",
        attempted.get("aggregate_gross_pnl_usdt") != "0.3300",
        attempted.get("aggregate_fees_usdt") != "0.2549248",
        attempted.get("aggregate_net_pnl_usdt") != "0.0859668",
        completed.get("normal_net_pnl_usdt") != "0.0108916",
        slot_four.get("terminal_account_authoritative") is not True,
        later_started,
    )):
        raise A0OfflineError(
            "pre-dispatch-cross failed A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "campaign_package_id": PRE_DISPATCH_CROSS_A2_PACKAGE_ID,
        "campaign_id": PRE_DISPATCH_CROSS_A2_CAMPAIGN_ID,
        "session_package_id": PRE_DISPATCH_CROSS_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": PRE_DISPATCH_CROSS_A2_HASHES,
        "failure_reason": failed["reason"],
        "failure_boundary": "slot_4_before_slot_5_authorization",
        "historical_pre_dispatch_cross_treated_as_session_failure": True,
        "historical_pre_dispatch_mutation_delta": 0,
        "historical_attempted_aggregate_component_mismatch": True,
        "historical_attempted_economic_attribution_reconciles": False,
        "corrected_attempted_normal_gross_pnl_usdt": "0.5960",
        "corrected_attempted_fees_usdt": "0.5100332",
        "corrected_attempted_net_pnl_usdt": "0.0859668",
        "later_slots_never_authorized_or_started": True,
        "normal_create_dispatches": 168,
        "normal_create_acknowledgements": 168,
        "normal_create_unresolved": 0,
        "mutation_retries": 0,
        "normal_bid_fills": 2,
        "normal_ask_fills": 2,
        "normal_fifo_round_trips": 2,
        "terminal_account_authoritative": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "two_flat_empty_snapshots": True,
        "repair_requires_fresh_identifiers": True,
        "rerun_or_resume_allowed": False,
        "production_authorized": False,
    }


def verify_interrupted_a2_predecessor(root: Path) -> dict[str, object]:
    campaign = root / FAILED_A2_ARTIFACT_ROOT / INTERRUPTED_A2_PACKAGE_ID
    session = (
        root / "artifacts" / "okx_demo_soak_validation"
        / INTERRUPTED_A2_SESSION_PACKAGE_ID
    )
    mapped = {
        "campaign/A2_PACKAGE_COMPLETED.json": (
            campaign / "A2_PACKAGE_COMPLETED.json"
        ),
        "campaign/completion_hashes.json": campaign / "completion_hashes.json",
        "campaign/specification/campaign_package_spec.json": (
            campaign / "specification/campaign_package_spec.json"
        ),
        "campaign/campaign_run/A2_CAMPAIGN_ARMED.json": (
            campaign / "campaign_run/A2_CAMPAIGN_ARMED.json"
        ),
        "campaign/campaign_run/state/supervisor_state.json": (
            campaign / "campaign_run/state/supervisor_state.json"
        ),
        "campaign/campaign_run/streams/supervisor_events.jsonl": (
            campaign / "campaign_run/streams/supervisor_events.jsonl"
        ),
        "campaign/campaign_run/registry/campaign_registry.jsonl": (
            campaign / "campaign_run/registry/campaign_registry.jsonl"
        ),
        "session/SOAK_PACKAGE_COMPLETED.json": (
            session / "SOAK_PACKAGE_COMPLETED.json"
        ),
        "session/campaign_authorization.json": (
            session / "campaign_authorization.json"
        ),
        "session/soak_run/SOAK_EXECUTION_ARMED.json": (
            session / "soak_run/SOAK_EXECUTION_ARMED.json"
        ),
        "session/soak_run/state/controller_state.jsonl": (
            session / "soak_run/state/controller_state.jsonl"
        ),
        "session/soak_run/state/validation_state.json": (
            session / "soak_run/state/validation_state.json"
        ),
        "session/soak_run/streams/events.jsonl": (
            session / "soak_run/streams/events.jsonl"
        ),
        "session/soak_run/state/lease.json": (
            session / "soak_run/state/lease.json"
        ),
        "session/soak_run/state/validation_state_journal.jsonl": (
            session / "soak_run/state/validation_state_journal.jsonl"
        ),
    }
    failures = [
        name
        for name, expected in INTERRUPTED_A2_HASHES.items()
        if not mapped[name].is_file() or _sha256(mapped[name]) != expected
    ]
    spec = _json(mapped["campaign/specification/campaign_package_spec.json"])
    state = _json(mapped["campaign/campaign_run/state/supervisor_state.json"])
    registry = _last_jsonl(
        mapped["campaign/campaign_run/registry/campaign_registry.jsonl"]
    )
    controller = dict(
        _last_jsonl(
            mapped["session/soak_run/state/controller_state.jsonl"]
        ).get("payload")
        or {}
    )
    validation = dict(
        _json(mapped["session/soak_run/state/validation_state.json"]).get(
            "payload"
        )
        or {}
    )
    ledger = dict(validation.get("ledger") or {})
    aggregate = dict(
        dict(registry.get("payload") or {}).get("aggregate_after_session") or {}
    )
    slots = list(spec.get("session_slots") or [])
    completed_terminal_missing: list[int] = []
    for row in slots[:7]:
        child = (
            root / "artifacts" / "okx_demo_soak_validation"
            / str(row.get("package_id") or "")
        )
        if not (child / "COMPLETED.json").is_file():
            completed_terminal_missing.append(int(row.get("slot") or 0))
    later_started: list[int] = []
    for row in slots[8:]:
        child = (
            root / "artifacts" / "okx_demo_soak_validation"
            / str(row.get("package_id") or "")
        )
        if (
            (child / "campaign_authorization.json").exists()
            or (child / "soak_run/SOAK_EXECUTION_ARMED.json").exists()
        ):
            later_started.append(int(row.get("slot") or 0))
    terminal_markers = (
        campaign / "campaign_run/A2_CAMPAIGN_COMPLETED.json",
        campaign / "campaign_run/completion_hashes.json",
        campaign / "campaign_run/decision/campaign_decision.json",
        session / "COMPLETED.json",
        session / "soak_run/FAILED.json",
        session / "soak_run/UNRESOLVED_FAILURE.json",
        session / "soak_run/terminal/account_snapshots.json",
    )
    if any((
        failures,
        spec.get("package_id") != INTERRUPTED_A2_PACKAGE_ID,
        spec.get("campaign_id") != INTERRUPTED_A2_CAMPAIGN_ID,
        len(slots) != 12,
        str(dict(slots[7]).get("package_id") or "")
        != INTERRUPTED_A2_SESSION_PACKAGE_ID,
        state.get("active_slot") != 8,
        state.get("completed_slots") != [1, 2, 3, 4, 5, 6, 7],
        state.get("failed_slots") != [],
        state.get("terminal_decision") is not None,
        state.get("terminal_account_authoritative") is not False,
        completed_terminal_missing,
        later_started,
        any(path.exists() for path in terminal_markers),
        controller.get("phase") != "CANCEL_RECONCILED",
        controller.get("controller_inventory_btc") != "-0.010",
        controller.get("owned_client_ids") != [],
        controller.get("pending_intent") != "",
        controller.get("normal_create_dispatches") != 31,
        controller.get("normal_create_acknowledgements") != 31,
        validation.get("phase") != "RUNNING",
        validation.get("last_reconciled_position_btc") != "-0.010",
        validation.get("pending_position_reconciliation") is not False,
        ledger.get("inventory_btc") != "-0.010",
        ledger.get("normal_bid_fills") != 1,
        ledger.get("normal_ask_fills") != 2,
        ledger.get("normal_gross_realized_pnl_usdt") != "0.6960",
        ledger.get("normal_fees_usdt") != "0.3807008",
        ledger.get("normal_net_realized_pnl_usdt") != "0.3152992",
        registry.get("campaign_id") != INTERRUPTED_A2_CAMPAIGN_ID,
        registry.get("event") != "SESSION_ACCEPTED",
        registry.get("sequence") != 8,
        aggregate.get("session_count") != 7,
        aggregate.get("normal_bid_fills") != 5,
        aggregate.get("normal_ask_fills") != 7,
        aggregate.get("normal_fill_count") != 12,
        aggregate.get("normal_fifo_round_trips") != 4,
        aggregate.get("normal_net_pnl_usdt") != "0.4763120",
        aggregate.get("aggregate_net_pnl_usdt") != "1.434066570",
        aggregate.get("special_flatten_sessions") != 4,
        aggregate.get("special_flatten_fraction")
        != "0.5714285714285714285714285714",
        aggregate.get("normal_create_dispatches") != 384,
        aggregate.get("normal_create_acknowledgements") != 384,
        aggregate.get("normal_create_unresolved") != 0,
        aggregate.get("unclassified_quote_mode_ticks") != 0,
        aggregate.get("unsafe_sessions") != 0,
    )):
        raise A0OfflineError(
            "interrupted A2 predecessor changed: "
            + ",".join(failures[:5])
        )
    return {
        "passed": True,
        "immutable": True,
        "interrupted": True,
        "campaign_incomplete": True,
        "campaign_package_id": INTERRUPTED_A2_PACKAGE_ID,
        "campaign_id": INTERRUPTED_A2_CAMPAIGN_ID,
        "session_package_id": INTERRUPTED_A2_SESSION_PACKAGE_ID,
        "fixed_hashes": INTERRUPTED_A2_HASHES,
        "active_slot": 8,
        "completed_slots": [1, 2, 3, 4, 5, 6, 7],
        "failed_slots": [],
        "later_slots_never_authorized_or_started": True,
        "authoritative_terminal_account_unknown": True,
        "last_durable_local_inventory_btc": "-0.010",
        "last_durable_owned_orders": 0,
        "last_durable_pending_intent": False,
        "normal_create_dispatches": 31,
        "normal_create_acknowledgements": 31,
        "normal_create_unresolved": 0,
        "completed_session_count": 7,
        "completed_normal_bid_fills": 5,
        "completed_normal_ask_fills": 7,
        "completed_normal_fifo_round_trips": 4,
        "completed_normal_net_pnl_usdt": "0.4763120",
        "completed_special_flatten_sessions": 4,
        "same_generation_resume_rerun_or_recovery_allowed": False,
        "fresh_read_only_preflight_required": True,
        "fresh_identifiers_required": True,
        "production_authorized": False,
    }


def requirement_matrix() -> list[dict[str, object]]:
    return [
        {
            "requirement": "source-bound append-only campaign registry",
            "implementation": "okx_demo_multi_session_campaign.CampaignRegistry",
            "tests": [
                "test_twelve_balanced_profitable_sessions_pass_and_reverify",
                "test_registry_truncation_hash_corruption_and_manifest_drift_are_rejected",
                "test_create_counter_extensions_round_trip_losslessly_through_registry",
            ],
        },
        {
            "requirement": "12-session aggregate wall/create/loss budgets",
            "implementation": "CampaignLimits and CampaignRegistry.evaluate",
            "tests": [
                "test_hard_kill_and_aggregate_loss_fail_closed_early",
                "test_identity_source_overlap_and_budget_drift_are_refused_without_append",
                "test_campaign_wall_budget_counts_inter_session_gaps_not_only_active_time",
            ],
        },
        {
            "requirement": "fresh source-bound A2 package with twelve predeclared sessions",
            "implementation": "okx_demo_multi_session_prepare.prepare",
            "tests": [
                "test_identifiers_predeclare_exactly_twelve_unique_bound_sessions",
                "test_prepare_freezes_campaign_and_children_without_execution_or_token_leak",
            ],
        },
        {
            "requirement": "single-instance sequential campaign supervisor",
            "implementation": "okx_demo_multi_session_supervisor.CampaignSupervisor",
            "tests": [
                "test_lease_collision_stale_recovery_and_single_active_session_gate",
                "test_twelve_sessions_are_sequential_and_terminal_marker_is_last",
            ],
        },
        {
            "requirement": "economic child requires durable campaign authorization",
            "implementation": "okx_demo_soak_executor._verify_campaign_authorization",
            "tests": [
                "test_economic_child_refuses_start_without_supervisor_gate_before_marker",
                "test_missing_child_terminal_artifact_fails_campaign_closed",
            ],
        },
        {
            "requirement": "spread/inventory/fee/markout/FIFO attribution",
            "implementation": "SessionEvidence exact Decimal identities",
            "tests": [
                "test_session_accounting_terminal_and_mutation_boundaries_fail_closed",
                "test_twelve_balanced_profitable_sessions_pass_and_reverify",
            ],
        },
        {
            "requirement": "classified quote modes and causal maker re-entry/work-off",
            "implementation": "EconomicSessionController",
            "tests": [
                "test_every_tick_including_hard_kill_is_classified_and_reconciles",
                "test_fill_defense_maker_reentry_workoff_and_markout_form_causal_chain",
            ],
        },
        {
            "requirement": "bounded DRAINING with 48 admission plus 12 maker work-off creates",
            "implementation": "EconomicSessionController SessionPhase and repaired executor",
            "tests": [
                "test_draining_partition_blocks_new_exposure_and_preserves_total_cap",
                "test_fee_positive_prices_and_quote_retention_are_decimal_exact",
            ],
        },
        {
            "requirement": "quantity/order-bound causal attribution and non-inflating FIFO",
            "implementation": "PendingCausalFill binding and FillLedger fragment/lot/cycle audit",
            "tests": [
                "test_partial_fifo_fragments_count_one_completed_lot_only",
                "test_reentry_order_binds_only_oldest_eligible_quantity",
            ],
        },
        {
            "requirement": "failed child attempted counters and terminal account reporting",
            "implementation": "BoundedSoakExecutor failure evidence and CampaignSupervisor ingestion",
            "tests": [
                "test_failure_manifest_writes_two_snapshots_and_attempted_counters_last",
                "test_supervisor_accounts_verified_failed_child_instead_of_zeroing_attempt",
            ],
        },
        {
            "requirement": "post-fill economic continuation without weakening formal R1/R2",
            "implementation": "EconomicSessionEngine specialization",
            "tests": [
                "test_formal_engine_keeps_r1_latch_while_economic_engine_releases_only_after_reconciliation",
                "test_economic_mode_continues_after_fill_and_proves_causal_round_trip",
            ],
        },
        {
            "requirement": "pre-dispatch fill-latch reconciliation and classified create acknowledgements",
            "implementation": (
                "EconomicSessionEngine.confirm_fill_reconciliation plus "
                "BoundedSoakExecutor dispatch/ack audit"
            ),
            "tests": [
                "test_simultaneous_two_sided_fill_reconciles_before_next_dispatch",
                "test_explicit_post_only_rejection_is_absent_without_retry_or_phantom_order",
                "test_closed_engine_gate_blocks_before_gateway_mutation",
            ],
        },
        {
            "requirement": "failed-session full economic attribution in attempted aggregate",
            "implementation": (
                "BoundedSoakExecutor failure evidence and CampaignSupervisor "
                "Decimal aggregation"
            ),
            "tests": [
                "test_failure_manifest_preserves_full_decimal_accounting_and_create_counters",
                "test_supervisor_accounts_verified_failed_child_instead_of_zeroing_attempt",
            ],
        },
        {
            "requirement": "account clock gate fails before mutation and preserves flat terminal evidence",
            "implementation": (
                "BoundedSoakExecutor prearm account clock gate plus immutable "
                "clock-failure audit"
            ),
            "tests": [
                "test_source_protocol_predecessors_and_matrices_are_exact",
                "test_prearm_clock_skew_fails_closed_without_mutation",
            ],
        },
        {
            "requirement": "accepted session evidence round-trips losslessly with exact create counters",
            "implementation": (
                "SessionEvidence extension preservation plus CampaignRegistry "
                "aggregate create-counter audit"
            ),
            "tests": [
                "test_create_counter_extensions_round_trip_losslessly_through_registry",
                "test_source_protocol_predecessors_and_matrices_are_exact",
            ],
        },
        {
            "requirement": "future-within-skew fills use monotonic causal timestamps",
            "implementation": (
                "BoundedSoakExecutor durable activity clock for defense, "
                "work-off, and markout continuation"
            ),
            "tests": [
                "test_future_fill_within_skew_uses_monotonic_causal_timestamp",
                "test_source_protocol_predecessors_and_matrices_are_exact",
            ],
        },
        {
            "requirement": "post-only price races block before dispatch without failing the session",
            "implementation": (
                "FormalDemoGateway typed PostOnlyWouldCross plus executor "
                "durable pre-dispatch blocked-placement continuation"
            ),
            "tests": [
                "test_crossed_post_only_quote_is_typed_and_blocked_before_dispatch",
                "test_pre_dispatch_cross_is_activity_block_not_session_failure",
                "test_source_protocol_predecessors_and_matrices_are_exact",
            ],
        },
        {
            "requirement": "attempted campaign economics reconcile completed and failed components",
            "implementation": (
                "Decimal attempted aggregate derives total gross, fees, and net "
                "from normal-versus-special components"
            ),
            "tests": [
                "test_attempted_aggregate_derives_total_components_from_normal_and_special",
                "test_source_protocol_predecessors_and_matrices_are_exact",
            ],
        },
        {
            "requirement": "interrupted active campaign freezes in place and requires fresh read-only reconciliation",
            "implementation": (
                "immutable interrupted-predecessor audit with no same-generation "
                "resume, rerun, or recovery"
            ),
            "tests": [
                "test_source_protocol_predecessors_and_matrices_are_exact",
                "test_multi_session_a1_uses_both_frozen_predecessors_not_legacy_root",
            ],
        },
        {
            "requirement": "fresh-session account rejection emits only sanitized account diagnostics",
            "implementation": (
                "OkxDemoAdapter pre-restore diagnostic capture plus preflight "
                "six-field whitelist"
            ),
            "tests": [
                "test_fresh_session_exposure_records_only_hashed_account_diagnostic",
                "test_failed_preflight_writes_sanitized_account_only_diagnostic",
                "test_invalid_account_only_diagnostic_is_omitted",
            ],
        },
        {
            "requirement": "finite ten-run operational failure matrix",
            "implementation": "FailureCampaignRegistry",
            "tests": [
                "test_exact_ten_group_matrix_passes_and_reverifies",
                "test_run_risk_and_prohibited_boundaries_are_fail_closed",
            ],
        },
        {
            "requirement": "zero mutation retry and explicit unresolved fail closed",
            "implementation": "FailureRunEvidence and existing real executor fixtures",
            "tests": [
                "test_unresolved_fail_closed_run_is_explicit_and_never_retries_mutation",
                "test_ambiguous_create_is_not_retried_and_owned_cancel_is_attempted",
            ],
        },
    ]


def scenario_matrix() -> dict[str, object]:
    return {
        "maximum_runs": 10,
        "maximum_run_minutes": 10,
        "maximum_run_normal_creates": 4,
        "maximum_campaign_normal_creates": 40,
        "maximum_unresolved_flatten_per_run": 1,
        "mutation_retry_attempts": 0,
        "groups": [
            {
                "group": group,
                "cases": [
                    {
                        "case": case,
                        "expected_decision": EXPECTED_DECISIONS[case],
                    }
                    for case in cases
                ],
            }
            for group, cases in SCENARIO_GROUPS.items()
        ],
        "A3_authorized": False,
        "network_allowed": False,
        "orders_allowed": False,
        "production_authorized": False,
    }


def _run_suite(
    root: Path,
    output: Path,
    name: str,
    arguments: tuple[str, ...],
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    temp = Path(tempfile.mkdtemp(prefix=f"okx-multi-session-{name}-"))
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    ):
        environment[variable] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *arguments,
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "okx_offline_network_guard",
        "--basetemp",
        str(temp),
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
        check=False,
    )
    _write_text(output / "tests" / f"{name}.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1,
        "live_endpoint_attempts": -1,
        "optuna_imported": True,
    }
    result: dict[str, object] = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": audit.get("network_attempts"),
        "live_endpoint_attempts": audit.get("live_endpoint_attempts"),
        "optuna_imported": audit.get("optuna_imported"),
        "command_scope": list(arguments),
        "root_exclusions": list(ROOT_EXCLUSIONS) if name == "root_non_optuna" else [],
        "root_deselect": list(ROOT_DESELECT) if name == "root_non_optuna" else [],
        "backtest_exclusions": (
            list(BACKTEST_EXCLUSIONS) if name == "backtest_non_optuna" else []
        ),
        "backtest_deselect": (
            list(BACKTEST_DESELECT) if name == "backtest_non_optuna" else []
        ),
    }
    result["passed_gate"] = all((
        result["returncode"] == 0,
        int(result["passed"]) > 0,
        result["network_attempts"] == 0,
        result["live_endpoint_attempts"] == 0,
        result["optuna_imported"] is False,
    ))
    return result


def run_suites(root: Path, output: Path) -> dict[str, dict[str, object]]:
    root_args = (
        "tests",
        *(f"--ignore={item}" for item in ROOT_EXCLUSIONS),
        *(f"--deselect={item}" for item in ROOT_DESELECT),
    )
    backtest_args = (
        "backtest/tests",
        *(f"--ignore={item}" for item in BACKTEST_EXCLUSIONS),
        *(f"--deselect={item}" for item in BACKTEST_DESELECT),
    )
    suites = {
        "multi_session_targeted": _run_suite(
            root, output, "multi_session_targeted", TARGETED
        ),
        "root_non_optuna": _run_suite(
            root, output, "root_non_optuna", root_args
        ),
        "backtest_non_optuna": _run_suite(
            root, output, "backtest_non_optuna", backtest_args
        ),
    }
    _write_json(output / "tests/test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise A0OfflineError("one or more A0 socket-denied suites failed")
    return suites


def _secret_scan(output: Path) -> dict[str, object]:
    pattern = re.compile(
        rb"(?i)OKX_(?:API_KEY|SECRET|PASSPHRASE)\s*[=:]\s*[^\s\"']+"
    )
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix == ".tmp":
            continue
        scanned += 1
        if pattern.search(path.read_bytes()):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_pattern_matches": sorted(set(matches)),
        "credential_environment_accessed": False,
        "credentials_serialized": False,
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {
        "completion_hashes.json",
        "A0_OFFLINE_BUILD_COMPLETED.json",
    }
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def run_offline(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not evidence_id.startswith("multi-session-a0-offline-"):
        raise A0OfflineError("A0 evidence identity is invalid")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise A0OfflineError("A0 evidence identity reuse refused")
    guard = SocketGuard()
    with guard:
        formal = verify_predecessor(root)
        soak = verify_soak_predecessor(root)
        failed_a2 = verify_failed_a2_predecessor(root)
        repair_failed_a2 = verify_repair_failed_a2_predecessor(root)
        clock_gate_failed_a2 = verify_clock_gate_failed_a2_predecessor(root)
        projection_failed_a2 = verify_registry_projection_failed_a2_predecessor(
            root
        )
        causal_clock_failed_a2 = verify_causal_clock_failed_a2_predecessor(root)
        pre_dispatch_cross_failed_a2 = (
            verify_pre_dispatch_cross_failed_a2_predecessor(root)
        )
        interrupted_a2 = verify_interrupted_a2_predecessor(root)
        hashes = source_hashes(root)
        output.mkdir(parents=True, exist_ok=False)
        _write_json(output / "predecessor/formal_audit.json", formal)
        _write_json(output / "predecessor/bounded_soak_audit.json", soak)
        _write_json(output / "predecessor/failed_a2_audit.json", failed_a2)
        _write_json(
            output / "predecessor/failed_a2_repair_audit.json",
            repair_failed_a2,
        )
        _write_json(
            output / "predecessor/failed_a2_clock_gate_audit.json",
            clock_gate_failed_a2,
        )
        _write_json(
            output / "predecessor/failed_a2_registry_projection_audit.json",
            projection_failed_a2,
        )
        _write_json(
            output / "predecessor/failed_a2_causal_clock_audit.json",
            causal_clock_failed_a2,
        )
        _write_json(
            output / "predecessor/failed_a2_pre_dispatch_cross_audit.json",
            pre_dispatch_cross_failed_a2,
        )
        _write_json(
            output / "predecessor/interrupted_a2_audit.json",
            interrupted_a2,
        )
        _write_json(output / "specification/source_hashes.json", hashes)
        _write_json(output / "specification/frozen_risk_budget.json", {
            "economic_campaign": {
                "maximum_sessions": 12,
                "maximum_wall_hours": 6,
                "maximum_normal_creates": 720,
                "aggregate_hard_loss_usdt": "75",
                "per_session_minutes": 30,
                "per_session_normal_creates": 60,
                "per_session_soft_hard_drawdown_usdt": ["22.50", "37.50"],
                "maximum_inventory_btc": "0.01",
                "maximum_owned_bid_ask": [1, 1],
                "read_retries": 3,
                "mutation_retries": 0,
            },
            "failure_campaign": {
                "maximum_runs": 10,
                "maximum_run_minutes": 10,
                "maximum_run_normal_creates": 4,
                "maximum_campaign_normal_creates": 40,
                "maximum_unresolved_flatten_per_run": 1,
                "mutation_retries": 0,
            },
            "production_authorized": False,
            "live_mode_available": False,
        })
        _write_json(output / "specification/requirement_matrix.json", {
            "requirements": requirement_matrix(),
            "A0_only": True,
        })
        _write_json(output / "specification/failure_scenario_matrix.json", scenario_matrix())
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise A0OfflineError("source changed during A0 suites")
        post_formal = verify_predecessor(root)
        post_soak = verify_soak_predecessor(root)
        post_failed_a2 = verify_failed_a2_predecessor(root)
        post_repair_failed_a2 = verify_repair_failed_a2_predecessor(root)
        post_clock_gate_failed_a2 = verify_clock_gate_failed_a2_predecessor(root)
        post_projection_failed_a2 = (
            verify_registry_projection_failed_a2_predecessor(root)
        )
        post_causal_clock_failed_a2 = verify_causal_clock_failed_a2_predecessor(
            root
        )
        post_pre_dispatch_cross_failed_a2 = (
            verify_pre_dispatch_cross_failed_a2_predecessor(root)
        )
        post_interrupted_a2 = verify_interrupted_a2_predecessor(root)
        if canonical_sha256(post_formal) != canonical_sha256(formal):
            raise A0OfflineError("formal predecessor changed during A0 suites")
        if canonical_sha256(post_soak) != canonical_sha256(soak):
            raise A0OfflineError("soak predecessor changed during A0 suites")
        if canonical_sha256(post_failed_a2) != canonical_sha256(failed_a2):
            raise A0OfflineError("failed A2 predecessor changed during A0 suites")
        if canonical_sha256(post_repair_failed_a2) != canonical_sha256(
            repair_failed_a2
        ):
            raise A0OfflineError(
                "repair failed A2 predecessor changed during A0 suites"
            )
        if canonical_sha256(post_clock_gate_failed_a2) != canonical_sha256(
            clock_gate_failed_a2
        ):
            raise A0OfflineError(
                "clock-gate failed A2 predecessor changed during A0 suites"
            )
        if canonical_sha256(post_projection_failed_a2) != canonical_sha256(
            projection_failed_a2
        ):
            raise A0OfflineError(
                "registry-projection failed A2 predecessor changed during A0 suites"
            )
        if canonical_sha256(post_causal_clock_failed_a2) != canonical_sha256(
            causal_clock_failed_a2
        ):
            raise A0OfflineError(
                "causal-clock failed A2 predecessor changed during A0 suites"
            )
        if canonical_sha256(
            post_pre_dispatch_cross_failed_a2
        ) != canonical_sha256(pre_dispatch_cross_failed_a2):
            raise A0OfflineError(
                "pre-dispatch-cross failed A2 predecessor changed during A0 suites"
            )
        if canonical_sha256(post_interrupted_a2) != canonical_sha256(
            interrupted_a2
        ):
            raise A0OfflineError(
                "interrupted A2 predecessor changed during A0 suites"
            )
        _write_json(output / "audits/source_hash_audit.json", {
            "passed": True,
            "source_files_checked": len(hashes),
            "source_manifest_sha256": canonical_sha256(hashes),
            "post_test_source_manifest_sha256": canonical_sha256(post_hashes),
            "root_successor_byte_identical": (
                hashes["AGENTS.md"] == hashes[SUCCESSOR_SOURCE]
            ),
        })
        _write_json(output / "audits/predecessor_immutability_audit.json", {
            "passed": True,
            "formal_sha256": canonical_sha256(formal),
            "post_formal_sha256": canonical_sha256(post_formal),
            "soak_sha256": canonical_sha256(soak),
            "post_soak_sha256": canonical_sha256(post_soak),
            "failed_a2_sha256": canonical_sha256(failed_a2),
            "post_failed_a2_sha256": canonical_sha256(post_failed_a2),
            "repair_failed_a2_sha256": canonical_sha256(repair_failed_a2),
            "post_repair_failed_a2_sha256": canonical_sha256(
                post_repair_failed_a2
            ),
            "clock_gate_failed_a2_sha256": canonical_sha256(
                clock_gate_failed_a2
            ),
            "post_clock_gate_failed_a2_sha256": canonical_sha256(
                post_clock_gate_failed_a2
            ),
            "registry_projection_failed_a2_sha256": canonical_sha256(
                projection_failed_a2
            ),
            "post_registry_projection_failed_a2_sha256": canonical_sha256(
                post_projection_failed_a2
            ),
            "causal_clock_failed_a2_sha256": canonical_sha256(
                causal_clock_failed_a2
            ),
            "post_causal_clock_failed_a2_sha256": canonical_sha256(
                post_causal_clock_failed_a2
            ),
            "pre_dispatch_cross_failed_a2_sha256": canonical_sha256(
                pre_dispatch_cross_failed_a2
            ),
            "post_pre_dispatch_cross_failed_a2_sha256": canonical_sha256(
                post_pre_dispatch_cross_failed_a2
            ),
            "interrupted_a2_sha256": canonical_sha256(interrupted_a2),
            "post_interrupted_a2_sha256": canonical_sha256(
                post_interrupted_a2
            ),
        })
        _write_json(output / "audits/test_count_audit.json", {
            name: value["passed"] for name, value in suites.items()
        })
        _write_json(output / "audits/endpoint_mutation_audit.json", {
            "mode": "OFFLINE_FIXTURE",
            "parent_socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
            "credential_accesses": 0,
            "preflight_preparations": 0,
            "preflight_attempts": 0,
            "economic_campaign_attempts": 0,
            "failure_campaign_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        secret = _secret_scan(output)
        _write_json(output / "audits/secret_scan.json", secret)
        if not secret["passed"]:
            raise A0OfflineError("secret pattern found in A0 evidence")
        decision = {
            "status": READY_STATUS,
            "evidence_id": evidence_id,
            "A0_passed": True,
            "economic_campaign_executed": False,
            "operational_failure_campaign_executed": False,
            "preflight_prepared": False,
            "preflight_executed": False,
            "network_attempts": 0,
            "credential_accesses": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "next_boundary": (
                "prepare fresh read-only Demo preflight identifiers entirely offline"
            ),
        }
        _write_json(output / "decision/a0_decision.json", decision)
        _write_text(
            output / "decision/a0_decision.md",
            "# OKX Demo multi-session A0 offline build\n\n"
            f"- Status: `{READY_STATUS}`\n"
            f"- Evidence ID: `{evidence_id}`\n"
            "- Network/credential/order activity: `0 / 0 / 0`\n"
            "- Preflight/economic/failure execution: `false / false / false`\n"
            "- Next boundary: prepare fresh read-only preflight identifiers offline\n",
        )
        completion = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        _write_json(output / "A0_OFFLINE_BUILD_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
            "requirement_count": len(requirement_matrix()),
            "failure_group_count": len(SCENARIO_GROUPS),
            "failure_case_count": sum(len(cases) for cases in SCENARIO_GROUPS.values()),
        })
    if guard.attempts:
        raise A0OfflineError("network attempt was blocked during A0 evidence")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id")
    args = parser.parse_args()
    evidence_id = args.evidence_id or (
        "multi-session-a0-offline-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline(args.root, evidence_id)
    except Exception as exc:
        print(f"MULTI_SESSION_A0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"evidence_id": evidence_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
