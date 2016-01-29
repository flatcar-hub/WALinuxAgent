# Microsoft Azure Linux Agent
#
# Copyright 2014 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Requires Python 2.4+ and Openssl 1.0+

import azurelinuxagent.utils.shellutil as shellutil
import azurelinuxagent.utils.textutil as textutil
import azurelinuxagent.logger as logger
from azurelinuxagent.distro.default.osutil import DefaultOSUtil
from azurelinuxagent.exception import OSUtilError


class FreeBSDOSUtil(DefaultOSUtil):
    def __init__(self):
        super(FreeBSDOSUtil, self).__init__()
        self._scsi_disks_timeout_set = False

    def restart_ssh_service(self):
        return shellutil.run('service sshd restart', chk_err=False)

    def useradd(self, username, expiration=None):
        """
        Create user account with 'username'
        """
        userentry = self.get_userentry(username)
        if userentry is not None:
            logger.warn("User {0} already exists, skip useradd", username)
            return

        if expiration is not None:
            cmd = "pw useradd {0} -e {1} -m".format(username, expiration)
        else:
            cmd = "pw useradd {0} -m".format(username)
        retcode, out = shellutil.run_get_output(cmd)
        if retcode != 0:
            raise OSUtilError(("Failed to create user account:{0}, "
                               "retcode:{1}, "
                               "output:{2}").format(username, retcode, out))

    def del_account(self, username):
        if self.is_sys_user(username):
            logger.error("{0} is a system user. Will not delete it.", username)
        shellutil.run('> /var/run/utx.active')
        shellutil.run('rmuser -y ' + username)
        self.conf_sudoer(username, remove=True)

    def chpasswd(self, username, password, crypt_id=6, salt_len=10):
        if self.is_sys_user(username):
            raise OSUtilError(("User {0} is a system user. "
                               "Will not set passwd.").format(username))
        passwd_hash = textutil.gen_password_hash(password, crypt_id, salt_len)
        cmd = "echo '{0}'|pw usermod {1} -H 0 ".format(passwd_hash, username)
        ret, output = shellutil.run_get_output(cmd, log_cmd=False)
        if ret != 0:
            raise OSUtilError(("Failed to set password for {0}: {1}"
                               "").format(username, output))

    def del_root_password(self):
        err = shellutil.run('pw mod user root -w no')
        if err:
            raise OSUtilError("Failed to delete root password: Failed to update password database.")

    def get_if_mac(self, ifname):
        data = self._get_net_info()
        if data[0] == ifname:
            return data[2].replace(':', '').upper()
        return None

    def get_first_if(self):
        return self._get_net_info()[:2]

    def route_add(self, net, mask, gateway):
        cmd = 'route add {0} {1} {2}'.format(net, gateway, mask)
        return shellutil.run(cmd, chk_err=False)

    def is_missing_default_route(self):
        """
        For FreeBSD, the default broadcast goes to current default gw, not a all-ones broadcast address, need to
        specify the route manually to get it work in a VNET environment.
        SEE ALSO: man ip(4) IP_ONESBCAST,
        """
        return True

    def set_route_for_dhcp_broadcast(self, ifname):
        return shellutil.run("route add 255.255.255.255 0.0.0.0 -ifp {0}".format(ifname), chk_err=False)

    def remove_route_for_dhcp_broadcast(self, ifname):
        shellutil.run("route delete 255.255.255.255", chk_err=False)

    def get_dhcp_pid(self):
        ret = shellutil.run_get_output("pgrep -n dhclient")
        return ret[1] if ret[0] == 0 else None

    def eject_dvd(self, chk_err=True):
        dvd = self.get_dvd_device()
        retcode = shellutil.run("cdcontrol -f {0} eject".format(dvd))
        if chk_err and retcode != 0:
            raise OSUtilError("Failed to eject dvd: ret={0}".format(retcode))

    def restart_if(self, ifname):
        # Restart dhclient only to publish hostname
        shellutil.run("/etc/rc.d/dhclient restart {0}".format(ifname), chk_err=False)

    def get_total_mem(self):
        cmd = "sysctl hw.physmem |awk '{print $2}'"
        ret, output = shellutil.run_get_output(cmd)
        if ret == 0:
            return int(output)/1024/1024
        else:
            raise OSUtilError("Failed to get total memory: {0}".format(output))

    def get_processor_cores(self):
        ret, output = shellutil.run_get_output("sysctl hw.ncpu |awk '{print $2}'")
        if ret:
            raise OSUtilError("Failed to get processor cores.")

        return int(output)

    def set_scsi_disks_timeout(self, timeout):
        if self._scsi_disks_timeout_set:
            return

        ret, output = shellutil.run_get_output('sysctl kern.cam.da.default_timeout={0}'.format(timeout))
        if ret:
            raise OSUtilError("Failed set SCSI disks timeout: {0}".format(output))
        self._scsi_disks_timeout_set = True

    def check_pid_alive(self, pid):
        return shellutil.run('ps -p {0}'.format(pid), chk_err=False) == 0

    @staticmethod
    def _get_net_info():
        """
        There is no SIOCGIFCONF
        on freeBSD - just parse ifconfig.
        Returns strings: iface, inet4_addr, and mac
        or 'None,None,None' if unable to parse.
        We will sleep and retry as the network must be up.
        """
        iface = ''
        inet = ''
        mac = ''

        err, output = shellutil.run_get_output('ifconfig -l ether', chk_err=False)
        if err:
            raise OSUtilError("Can't find ether interface:{0}".format(output))
        ifaces = output.split()
        if not ifaces:
            raise OSUtilError("Can't find ether interface.")
        iface = ifaces[0]

        err, output = shellutil.run_get_output('ifconfig ' + iface, chk_err=False)
        if err:
            raise OSUtilError("Can't get info for interface:{0}".format(iface))

        for line in output.split('\n'):
            if line.find('inet ') != -1:
                inet = line.split()[1]
            elif line.find('ether ') != -1:
                mac = line.split()[1]
        logger.verb("Interface info: ({0},{1},{2})", iface, inet, mac)

        return iface, inet, mac
