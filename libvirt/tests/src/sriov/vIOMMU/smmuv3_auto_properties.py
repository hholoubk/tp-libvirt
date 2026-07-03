import json

from virttest import utils_net, virsh, xml_utils
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.viommu import viommu_base

QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"


def add_qemu_cmdline_args(vm, args):
    """
    Append qemu:commandline arguments to the inactive domain XML.

    :param vm: VM object
    :param args: list of argument strings
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    root = vmxml.xmltreefile.getroot()
    if root.get("xmlns:qemu") is None:
        root.set("xmlns:qemu", QEMU_NS)

    cmdline = None
    for child in root:
        if child.tag.endswith("commandline"):
            cmdline = child
            break
    if cmdline is None:
        cmdline = xml_utils.ElementTree.SubElement(
            root, "{%s}commandline" % QEMU_NS)

    for arg in args:
        arg_elem = xml_utils.ElementTree.SubElement(
            cmdline, "{%s}arg" % QEMU_NS)
        arg_elem.set("value", arg)
    vmxml.sync()


def qom_get(vm_name, path, prop):
    """
    Read a QOM property from the running domain.

    :param vm_name: domain name
    :param path: QOM path
    :param prop: property name
    :return: property value
    """
    cmd = json.dumps({
        "execute": "qom-get",
        "arguments": {"path": path, "property": prop},
    })
    res = virsh.qemu_monitor_command(
        vm_name, cmd, debug=True, ignore_status=False).stdout_text
    data = json.loads(res)
    if "return" not in data:
        raise RuntimeError("qom-get failed for %s.%s: %s" % (path, prop, res))
    return data["return"]


def normalize_prop_value(prop, value):
    """
    Normalize SMMU property values for comparison.

    :param prop: property name
    :param value: raw qom-get return value
    :return: normalized string
    """
    if prop in ("ats", "ril"):
        if value in (False, 0, "0", "off"):
            return "off"
        if value in (True, 1, "1", "on"):
            return "on"
        return str(value).lower()
    return str(value)


def find_smmuv3_qom_path(vm_name, device_id=None):
    """
    Locate the arm-smmuv3 QOM path for the running domain.

    :param vm_name: domain name
    :param device_id: optional user-defined device id
    :return: QOM path string
    """
    if device_id:
        return "/machine/peripheral/%s" % device_id

    for root_path in ("/machine/peripheral", "/machine/unattached"):
        list_cmd = json.dumps({
            "execute": "qom-list",
            "arguments": {"path": root_path},
        })
        res = virsh.qemu_monitor_command(
            vm_name, list_cmd, debug=True, ignore_status=False).stdout_text
        for item in json.loads(res).get("return", []):
            if item.get("type") == "arm-smmuv3":
                name = item.get("name")
                if name:
                    return "%s/%s" % (root_path, name)
    raise RuntimeError("arm-smmuv3 device not found via qom-list")


def verify_smmu_props(test, vm_name, qom_path, expected):
    """
    Verify resolved SMMU properties match expected accel=off defaults.

    :param test: test object
    :param vm_name: domain name
    :param qom_path: QOM path to arm-smmuv3
    :param expected: dict of property name to expected value
    """
    for prop, exp in expected.items():
        actual = qom_get(vm_name, qom_path, prop)
        norm = normalize_prop_value(prop, actual)
        exp_norm = normalize_prop_value(prop, exp)
        test.log.info(
            "SMMU %s: got %r (normalized %s), expected %s",
            prop, actual, norm, exp_norm)
        if norm != exp_norm:
            test.fail(
                "SMMU property %s: expected %s, got %s (raw %r)"
                % (prop, exp_norm, norm, actual))


def run(test, params, env):
    """
    Verify SMMUv3 auto property resolution for accel=off.

    With accel=off, ats/ril/ssidsize/oas set to auto (explicitly or by
    default) must resolve to ats=off, ril=on, oas=44, ssidsize=0.
    """
    use_libvirt_iommu = params.get("use_libvirt_iommu", "yes") == "yes"
    iommu_dict = eval(params.get("iommu_dict", "{}"))
    smmu_device_args = params.get("smmu_device_args")
    smmu_device_id = params.get("smmu_device_id", "smmuv3.0")
    expected_props = eval(params.get("expected_smmu_props", "{}"))
    ping_dest = params.get("ping_dest")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, "iommu")
        if use_libvirt_iommu:
            test_obj.setup_iommu_test(iommu_dict=iommu_dict)
        else:
            add_qemu_cmdline_args(vm, ["-device", smmu_device_args])

        iface_dict = test_obj.parse_iface_dict()
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm.name), "interface", iface_dict)

        test.log.info("TEST_STEP: Start VM with SMMUv3 auto properties.")
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()

        qom_path = find_smmuv3_qom_path(
            vm.name, None if use_libvirt_iommu else smmu_device_id)
        test.log.info("TEST_STEP: Verify resolved SMMU properties at %s.",
                      qom_path)
        verify_smmu_props(test, vm.name, qom_path, expected_props)

        test.log.info("TEST_STEP: Verify guest IOMMU group and connectivity.")
        vm_session = vm.wait_for_serial_login(
            timeout=int(params.get("login_timeout")))
        vm_session.cmd("dmesg | grep -i 'Adding to iommu group'")
        libvirt.check_qemu_cmd_line("arm-smmuv3|iommu=smmuv3")

        s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=vm_session)
        if s:
            test.fail(
                "Failed to ping %s! status: %s, output: %s."
                % (ping_dest, s, o))
    finally:
        test_obj.teardown_iommu_test()
