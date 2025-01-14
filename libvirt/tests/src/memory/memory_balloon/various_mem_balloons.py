from virttest import virsh
from virttest import utils_libvirtd
import re

from avocado.utils import process

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memballoon
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virsh_cmd_check import virsh_cmd_check_base


def run(test, params, env):
    """
    Verify no error msg prompts without guest memory balloon driver.
    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    """

    def setup_vm_memory():
        """
        Setup general vm memory and current memory tags  according params - doesn't change the VM if not set
        """

        # TODO do we need to check if the memory is available on host?
        # setup memory according fixed settings
        vm_memory_attrs = {}
        try:
            mem_unit = params.get("mem_unit", "")
            mem_value = params.get("mem_value","")
            if mem_unit == "" or mem_value == "":
                test.log.debug("Memory unit and/or memory value not provided, xml NOT changed")
            else: 
                vm_memory_attrs['memory_unit'] = mem_unit
                vm_memory_attrs['memory'] = int(mem_value)
            current_mem_unit = params.get("current_mem_unit", "")
            current_mem = params.get("current_mem", "")
            if current_mem_unit == "" or current_mem == "":
                test.log.debug("Current Memory unitm and/or value not provided, xml NOT changed")
            else:
                vm_memory_attrs['current_mem_unit'] = current_mem_unit
                vm_memory_attrs['current_mem'] = int(current_mem)
        except Exception as e:
            test.log.error(f"Wrong test configuration: {(str(e))}")
        test.log.debug(f"setting memory if provided(count: {(len(vm_memory_attrs))}) (mem: {mem_unit}, {mem_value}, " +
                       f"current: {current_mem_unit}, {current_mem}")
        if len(vm_memory_attrs)>0:
            vmxml.setup_attrs(**vm_memory_attrs)

    def setup_vm_memballoon():
        """
        Setup vm memballoon inside the vm according params
        """
        # not sure if it can be empty, but assuming not ... we will use the "" as not set
        # TODO question: in other tests there is first the memballoon device removed
        if memballoon_model == "" or memballoon_alias_name =="":
            test.error(f"Wrong test configuration: memballoonloon model and alias are mandatory. Provided values: model ({memballoon_model}), alias ({memballoon_alias_name}).")

        memballoon_stats_period = params.get("memballoon_stats_period","10")
        test.log.debug(f"Setting add memballoonloon ({memballoon_model}, {memballoon_alias_name}).")
        balloon_dict = {
            'membal_model': memballoon_model,
            'membal_alias_name': memballoon_alias_name,
            'membal_stats_period': memballoon_stats_period
        }

        libvirt.update_memballoon_xml(vmxml, balloon_dict)

    def check_qemu_cmd_line():
        # org_umask = process.run('umask', verbose=True).stdout_text.strip()
        test.log.info("TEST_STEP 4. Check the qemu cmd line")
        try:
            qemu_cmd = "ps -ef | grep qemu-kvm | grep -v grep"
            qemu_cmd_output = process.run(qemu_cmd,shell=True).stdout_text.strip()

            memballoon_driver = params.get("memballoon_driver","")
            # TODO if memballoon_driver == "":
                # variant with memballoon type none
            pattern = fr'-device\s+{{"driver":"({memballoon_driver}[^"]*)".*?"id":"([^"]+)".*?}}' 
            matches = re.findall(pattern, qemu_cmd_output)

            device_found = False
            if memballoon_model == "none" and len(matches)>0:
                driver, device_id = matches[0]
                test.fail(f"For memballoon_model == none, there shouldn't be device with balloon related driver ({driver}) and id ({device_id})")

            for _, device_id in matches: 
                if device_id == memballoon_alias_name:
                    device_found = True
                    test.log.info(f"Found correct id: {device_id} with expected memballoon_driver")
                else:
                    test.log.debug(f"Found device with expected memballoon_driver ({memballoon_driver}) but different ID {device_id}")
            if not device_found:
                test.fail(f"There is no device for driver ({memballoon_driver}) and id ({device_id})")

        except Exception as e:
            test.error(f"Failed with: {str(e)}")

    def check_device_on_guest(): 
        test.log.info("TEST_STEP 5. Check device exists on guest")
        guest_cmd = "lspci -vvv | grep balloon"
        guest_session = vm.wait_for_login()

        status, stdout = guest_session.cmd_status_output(guest_cmd) 
        guest_session.close()
        test.log.debug(f"guest cmd result: {status}, stdout: {stdout}")

        if status != 0:
            test.fail("Failed to run lscpi command on guest and get device info: ")

        # check returned stdout contains memory balloon string 
        # the searched string can be set in configuration, or it is by deafult memory balloon
        memballoon_device_str = params.get("memballoon_device_str","memory balloon")
        if not memballoon_model == "none":
            if not re.search(memballoon_device_str, stdout):
                test.fail(f"Expected string {memballoon_device_str} was not returned from guest lspci command. Returned: ")

        # (or is empty for "none" model
        else:
            if not stdout == "":
                test.fail(f"There shouldn't be any device returned from guest lscpi command with memballoon model none.")
            
    def setup_and_start_vm():
        # setup memory and current memory according fixed settings
        test.log.info("TEST_STEP 1. define VM")
        setup_vm_memory()

        # setup memballoon according provided parameters
        setup_vm_memballoon()

        test.log.info("TEST_STEP 2. start VM")
        vm.start() 

    def check_memballoon_xml():
        test.log.debug('TEST_STEP 3. check VM XML for memballoon element')
        # expect_xpath = [ {'element_attrs': [f".//memballoon[@model='{memballoon_model}']"]},
        expect_xpath = [{'element_attrs': [f".//memballoon[@model='{memballoon_model}']/alias[@name='{memballoon_alias_name}']"]}]
        xpath_exists = True
        # TODO check for none model
        if not libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, expect_xpath, ignore_status=True) == xpath_exists:
            test.fail("Expect to get '%s' in xml " % expect_xpath)
        test.log.info('Correct memballoon element found in XML')


    def run_test():
        """
        Setup vm memballoon device model and aliase according params

        :params vm_name: VM name (for debug log).
        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """

        check_memballoon_xml() # TEST_STEP 3. check VM XML for memballoon element
        check_qemu_cmd_line() # TEST_STEP 4. Check the qemu cmd )
        check_device_on_guest() # TEST_STEP 5. Check device exists on guest

        # TODO .. how does it work with tmp_data_dir? 
        save_file_name = "/tmp/avocado.save"
        test.log.debug('TEST_STEP 6. save and restore VM ')
        virsh_helper.check_save_restore(save_file_name)

        test.log.debug('TEST_STEP 7. restart virtqemud') 
        if not libvirtd.restart():
            test.fail("fail to restart libvirtd")

        # TODO .. not relevant for "none model ... 
        test.log.debug('TEST_STEP 8. check disk/memory cache ')
        virsh_helper.check_disk_caches()


    def run_test2():
        """
        Define and start guest
        Check No error msg prompts without guest memory balloon driver.
        """

        test.log.info("TEST_STEP3: Remove virtio_balloon module in guest")
        remove_module(module)

        test.log.info("TEST_STEP4: Change guest current memory allocation")
        result = virsh.setmem(domain=vm_name, size=set_mem, debug=True)
        libvirt.check_exit_status(result)

        test.log.info("TEST_STEP5: Check memory allocation is not changed")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

    def teardown_test():
        """
        Clean data.
        """
        test.log.debug(virsh.dumpxml(vm_name).stdout_text)
        test.log.info("TEST_TEARDOWN: Clean up env.")
        virsh_helper.teardown()
        bkxml.sync()

    test.log.info("TEST_SETUP: backup and define: guest ")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    virsh_helper = virsh_cmd_check_base.VirshCmdCheck(vm, vm_name, params,test)
    libvirtd = utils_libvirtd.Libvirtd("virtqemud")
    bkxml = vmxml.copy()

    memballoon_model = params.get("memballoon_model", "")
    memballoon_alias_name = params.get("memballoon_alias_name", "")

    try:
        setup_and_start_vm()
        run_test()

    finally:
        teardown_test()



