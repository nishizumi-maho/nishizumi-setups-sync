from __future__ import annotations

from nishizumi_sync import cars


def test_clean_name_strips_invalid_characters():
    assert cars.clean_name(' My/Team: "A" ') == "MyTeam A"
    assert cars.clean_name("trailing dots...") == "trailing dots"
    assert cars.clean_name(None) == ""


def test_clean_name_removes_control_characters():
    assert cars.clean_name("a\x07b") == "ab"
    assert cars.clean_name("line\tbreak") == "line break"


def test_clean_name_escapes_reserved_windows_names():
    assert cars.clean_name("CON") == "CON_"
    assert cars.clean_name("lpt1") == "lpt1_"


def test_numeric_prefixes_do_not_break_matching():
    assert cars.identify_setup("03 - Ferrari GT3") == ["ferrari296gt3"]
    assert cars.identify_setup("12_Porsche GTD") == ["porsche992rgt3"]


def test_longest_alias_wins():
    # "bmw gt4 evo" must not be swallowed by a shorter, less specific key.
    assert cars.identify_setup("BMW GT4 Evo") == ["bmwm4evogt4"]


def test_iracing_folder_names_resolve_to_themselves():
    assert cars.identify_setup("dallarair18") == ["dallarair18"]


def test_group_folders_resolve_to_every_variant():
    assert cars.identify_setup("NASCAR Trucks") == cars.CAR_GROUPS["nascar trucks"]
    assert cars.identify_setup("Xfinity") == cars.CAR_GROUPS["nascar xfinity"]
    assert cars.identify_setup("07 - SuperFormula SF23") == cars.CAR_GROUPS["superformula sf23"]


def test_unknown_folder_returns_nothing():
    assert cars.identify_setup("Some Random Folder") == []
    assert cars.identify_setup("") == []


def test_custom_mapping_takes_precedence_and_expands_groups():
    mapping = {"my special car": "ferrari499p", "team truck": "nascar trucks"}
    assert cars.identify_setup("My Special Car", mapping) == ["ferrari499p"]
    assert cars.identify_setup("Team Truck", mapping) == cars.CAR_GROUPS["nascar trucks"]


def test_group_for_car():
    assert cars.group_for_car("trucks fordf150") == cars.CAR_GROUPS["nascar trucks"]
    assert cars.group_for_car("ferrari296gt3") is None


def test_custom_mapping_round_trip(logger):
    path = cars.save_custom_mapping({"My Folder": "ferrari296gt3", "empty": ""}, logger=logger)
    assert cars.load_custom_mapping(path, logger=logger) == {"my folder": "ferrari296gt3"}


def test_broken_mapping_file_is_ignored(tmp_path, logger):
    path = tmp_path / "map.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert cars.load_custom_mapping(path, logger=logger) == {}


def test_unmapped_folders():
    assert cars.unmapped_folders(["Ferrari GT3", "Nope"]) == ["Nope"]


def test_known_targets_are_unique_and_sorted():
    targets = cars.known_targets()
    assert targets == sorted(set(targets))
    assert "ferrari296gt3" in targets
