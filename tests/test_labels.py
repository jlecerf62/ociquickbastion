from qb2.labels import compartment_path_labels, sort_compartments_by_hierarchy


def test_compartment_path_labels_omit_root_from_child_paths():
    compartments = [
        {"id": "root", "name": "tenancy", "compartment_id": None},
        {"id": "a", "name": "dev", "compartment_id": "root"},
        {"id": "b", "name": "app", "compartment_id": "a"},
    ]

    labels = compartment_path_labels(compartments)

    assert labels["root"] == "tenancy"
    assert labels["a"] == "dev"
    assert labels["b"] == "dev / app"


def test_compartment_path_labels_disambiguate_duplicate_paths():
    compartments = [
        {"id": "root", "name": "tenancy", "compartment_id": None},
        {"id": "ocid1.compartment.oc1..aaaa11112222", "name": "dev", "compartment_id": "root"},
        {"id": "ocid1.compartment.oc1..bbbb33334444", "name": "dev", "compartment_id": "root"},
    ]

    labels = compartment_path_labels(compartments)

    assert labels["ocid1.compartment.oc1..aaaa11112222"] == "dev [aaaa11112222]"
    assert labels["ocid1.compartment.oc1..bbbb33334444"] == "dev [bbbb33334444]"


def test_sort_compartments_by_hierarchy_orders_siblings_alphabetically():
    compartments = [
        {"id": "z", "name": "zeta", "compartment_id": "root"},
        {"id": "b", "name": "beta", "compartment_id": "a"},
        {"id": "root", "name": "tenancy", "compartment_id": None},
        {"id": "a", "name": "alpha", "compartment_id": "root"},
        {"id": "c", "name": "charlie", "compartment_id": "a"},
    ]

    ordered = sort_compartments_by_hierarchy(compartments)

    assert [c["id"] for c in ordered] == ["root", "a", "b", "c", "z"]
