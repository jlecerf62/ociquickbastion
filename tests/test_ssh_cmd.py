from qb2.ssh import build_ssh_command


def test_build_pfwd_cmd_contains_expected_bits():
    cmd = build_ssh_command(
        mode="PFWD",
        ssh_private_key="/home/user/.ssh/id_rsa",
        http_proxy=None,
        local_port=4444,
        instance_ip="10.0.0.1",
        target_port=22,
        bastion_user_name="abc",
        session_id="ocid1.bastionsession.oc1.eu-frankfurt-1.xyz",
        region="eu-frankfurt-1",
    )
    assert "-N -L 4444:10.0.0.1:22" in cmd
    assert "host.bastion.eu-frankfurt-1.oci.oraclecloud.com" in cmd


def test_build_socks_cmd_contains_expected_bits():
    cmd = build_ssh_command(
        mode="SOCKS",
        ssh_private_key="/home/user/.ssh/id_rsa",
        http_proxy=None,
        local_port=1080,
        instance_ip=None,
        target_port=22,
        bastion_user_name="abc",
        session_id="ocid1.bastionsession.oc1.eu-frankfurt-1.xyz",
        region="eu-frankfurt-1",
    )
    assert "-N -D 127.0.0.1:1080" in cmd
    assert "host.bastion.eu-frankfurt-1.oci.oraclecloud.com" in cmd


def test_build_managed_ssh_contains_proxycommand():
    cmd = build_ssh_command(
        mode="SSH",
        ssh_private_key="/home/user/.ssh/id_rsa",
        http_proxy=None,
        local_port=None,
        instance_ip="10.0.0.2",
        target_port=22,
        bastion_user_name="abc",
        session_id="ocid1.bastionsession.oc1.eu-frankfurt-1.xyz",
        region="eu-frankfurt-1",
    )
    assert "-o ProxyCommand=\"ssh -i /home/user/.ssh/id_rsa" in cmd
    assert "-W %h:%p -p 22 ocid1.bastionsession" in cmd
    assert "abc@10.0.0.2" in cmd
