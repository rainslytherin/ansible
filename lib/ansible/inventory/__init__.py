# -*- coding: utf-8 -*-
# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

#############################################
import fnmatch
import os
import sys
import re
import subprocess

import ansible.constants as C
from ansible.inventory.ini import InventoryParser
from ansible.inventory.script import InventoryScript
from ansible.inventory.dir import InventoryDirectory
from ansible.inventory.group import Group
from ansible.inventory.host import Host
from ansible import errors
from ansible import utils

class Inventory(object):
    """
    Host inventory for ansible.
    """
    # python的 __slots__ 内置函数用来对类属性进行限制，只有在__slots__ 中显示声明的方法、属性才可以被访问。
    # 常用来进行属性限制或内存优化
    # 使用 __slots__ 的类会删除__dict__属性，从而来优化类实例对内存的需求
    __slots__ = [ 'host_list', 'groups', '_restriction', '_also_restriction', '_subset',
                  'parser', '_vars_per_host', '_vars_per_group', '_hosts_cache', '_groups_list',
                  '_pattern_cache', '_vault_password', '_vars_plugins', '_playbook_basedir']

    def __init__(self, host_list=C.DEFAULT_HOST_LIST, vault_password=None):

        # the host file file, or script path, or list of hosts
        # if a list, inventory data will NOT be loaded
        # host_list 有很多中类型
        self.host_list = host_list
        self._vault_password=vault_password

        # caching to avoid repeated calculations, particularly with
        # external inventory scripts.
        # 缓存一些数据以避免重复计算，特别是使用外部库存脚本。

        self._vars_per_host  = {} # 每个主机变量
        self._vars_per_group = {} # 每个group 变量
        self._hosts_cache    = {} # 主机列表缓存
        self._groups_list    = {} # group 列表缓存
        self._pattern_cache  = {} # pattern 模式缓存

        # to be set by calling set_playbook_basedir by playbook code
        self._playbook_basedir = None

        # the inventory object holds a list of groups
        self.groups = []

        # a list of host(names) to contain current inquiries to
        self._restriction = None
        self._also_restriction = None
        self._subset = None

        # 如果host_list是字符串，则根据逗号进行分隔成列表
        if isinstance(host_list, basestring):
            if "," in host_list:
                host_list = host_list.split(",")
                host_list = [ h for h in host_list if h and h.strip() ]

        if host_list is None:
            self.parser = None

        # 如果host_list是列表，创建一个all group，并将host加入all group，支持IPV6
        elif isinstance(host_list, list):
            self.parser = None
            all = Group('all')
            self.groups = [ all ]
            ipv6_re = re.compile('\[([a-f:A-F0-9]*[%[0-z]+]?)\](?::(\d+))?')
            for x in host_list:
                m = ipv6_re.match(x)
                if m:
                    all.add_host(Host(m.groups()[0], m.groups()[1]))
                else:
                    if ":" in x:
                        tokens = x.rsplit(":", 1)
                        # if there is ':' in the address, then this is an ipv6
                        if ':' in tokens[0]:
                            all.add_host(Host(x))
                        else:
                            all.add_host(Host(tokens[0], tokens[1]))
                    else:
                        all.add_host(Host(x))
        # 如果host_list是文件或文件夹
        elif os.path.exists(host_list):
            if os.path.isdir(host_list):
                # Ensure basedir is inside the directory
                # 如果host_list是目录parser使用 InventoryDirectory 目录解析
                self.host_list = os.path.join(self.host_list, "")
                self.parser = InventoryDirectory(filename=host_list)
                self.groups = self.parser.groups.values()
            else:
                # check to see if the specified file starts with a
                # shebang (#!/), so if an error is raised by the parser
                # class we can show a more apropos error
                # 如果host_list是文件，则判断文件是否以shebang开头
                shebang_present = False
                try:
                    inv_file = open(host_list)
                    first_line = inv_file.readlines()[0]
                    inv_file.close()
                    if first_line.startswith('#!'):
                        shebang_present = True
                except:
                    pass

                if utils.is_executable(host_list):
                    try:
                        # 如果host_list是可执行文件，parser使用InventoryScript解析
                        self.parser = InventoryScript(filename=host_list)
                        self.groups = self.parser.groups.values()
                    except:
                        if not shebang_present:
                            raise errors.AnsibleError("The file %s is marked as executable, but failed to execute correctly. " % host_list + \
                                                      "If this is not supposed to be an executable script, correct this with `chmod -x %s`." % host_list)
                        else:
                            raise
                else:
                    try:
                        # 如果host_list是普通文本文件，parser使用 InventoryParser 解析
                        self.parser = InventoryParser(filename=host_list)
                        self.groups = self.parser.groups.values()
                    except:
                        if shebang_present:
                            raise errors.AnsibleError("The file %s looks like it should be an executable inventory script, but is not marked executable. " % host_list + \
                                                      "Perhaps you want to correct this with `chmod +x %s`?" % host_list)
                        else:
                            raise

            utils.plugins.vars_loader.add_directory(self.basedir(), with_subdir=True)
        else:
            raise errors.AnsibleError("Unable to find an inventory file, specify one with -i ?")

        self._vars_plugins = [ x for x in utils.plugins.vars_loader.all(self) ] # 加载vars plugin，这里先跳过

        # get group vars from group_vars/ files and vars plugins
        # 从group_vars/ 目录的文件和vars plugins中获取group 变量
        for group in self.groups:
            group.vars = utils.combine_vars(group.vars, self.get_group_variables(group.name, vault_password=self._vault_password))

        # get host vars from host_vars/ files and vars plugins
        # 从host_vars/ 目录的文件和vars plugins中获取host变量
        for host in self.get_hosts():
            host.vars = utils.combine_vars(host.vars, self.get_host_variables(host.name, vault_password=self._vault_password))


    def _match(self, str, pattern_str):
        try:
            if pattern_str.startswith('~'):
                return re.search(pattern_str[1:], str)
            else:
                return fnmatch.fnmatch(str, pattern_str)
        except Exception, e:
            raise errors.AnsibleError('invalid host pattern: %s' % pattern_str)

    def _match_list(self, items, item_attr, pattern_str):
        results = []
        try:
            if not pattern_str.startswith('~'):
                pattern = re.compile(fnmatch.translate(pattern_str))
            else:
                pattern = re.compile(pattern_str[1:])
        except Exception, e:
            raise errors.AnsibleError('invalid host pattern: %s' % pattern_str)

        for item in items:
            if pattern.match(getattr(item, item_attr)):
                results.append(item)
        return results

    def get_hosts(self, pattern="all"):
        """ 
        find all host names matching a pattern string, taking into account any inventory restrictions or
        applied subsets.
        找到所有与pattern匹配的的主机列表，需要考虑inventory限制和subset。
        """

        # process patterns
        if isinstance(pattern, list): # 这里把pattern从列表变成字符串又变回列表了。。。
            pattern = ';'.join(pattern)
        patterns = pattern.replace(";",":").split(":") # pattern可以用冒号连接，比如 webservers:phoenix
        hosts = self._get_hosts(patterns) # 获取Host对象列表

        # exclude hosts not in a subset, if defined
        # 如果输入了subset则进行过滤
        if self._subset:
            subset = self._get_hosts(self._subset) # 获取匹配subset的主机对象列表
            hosts = [ h for h in hosts if h in subset ] # 在hosts中找到存在与subset的主机列表

        # exclude hosts mentioned in any restriction (ex: failed hosts)
        # 排除任何限制中提到的主机，self._restriction在哪里进行定义的？
        if self._restriction is not None:
            hosts = [ h for h in hosts if h.name in self._restriction ]
        if self._also_restriction is not None:
            hosts = [ h for h in hosts if h.name in self._also_restriction ]

        return hosts

    def _get_hosts(self, patterns):
        """
        finds hosts that match a list of patterns. Handles negative
        matches as well as intersection matches.
        host patterns可以分为一下几种情况：
        a:b , a和b的并集 , {x∣x∈a,或 x∈b}
        a:&b , a和b的交集 ，{x∣x∈a,且x∈b}
        a:!b , a和b的差集，既在a中却不在b中的， {x∣x∈a,且x∉b}

        """

        # Host specifiers should be sorted to ensure consistent behavior
        pattern_regular = [] # 正常的pattern
        pattern_intersection = [] # 并集pattern
        pattern_exclude = [] # 差集pattern
        for p in patterns:
            if p.startswith("!"):
                pattern_exclude.append(p)
            elif p.startswith("&"):
                pattern_intersection.append(p)
            elif p:
                pattern_regular.append(p)

        # if no regular pattern was given, hence only exclude and/or intersection
        # make that magically work
        # 如果没有输入正常的pattern，只有交集或差集，则将all设为正常的pattern
        if pattern_regular == []:
            pattern_regular = ['all']

        # when applying the host selectors, run those without the "&" or "!"
        # first, then the &s, then the !s.
        # 先求正规的pattern的并集，然后是交集，最后是差集
        patterns = pattern_regular + pattern_intersection + pattern_exclude

        hosts = []

        for p in patterns:
            # avoid resolving a pattern that is a plain host

            if p in self._hosts_cache:  # 如果p是一个已经缓存在self._hosts_cache中的主机名
                hosts.append(self.get_host(p)) # 获取Host对象并添加到hosts立碑中
            else:
                that = self.__get_hosts(p)
                if p.startswith("!"):
                    hosts = [ h for h in hosts if h not in that ]
                elif p.startswith("&"):
                    hosts = [ h for h in hosts if h in that ]
                else:
                    to_append = [ h for h in that if h.name not in [ y.name for y in hosts ] ]
                    hosts.extend(to_append)
        return hosts

    def __get_hosts(self, pattern):
        """ 
        finds hosts that positively match a particular pattern.  Does not
        take into account negative matches.
        """

        if pattern in self._pattern_cache:
            return self._pattern_cache[pattern]

        (name, enumeration_details) = self._enumeration_info(pattern)
        hpat = self._hosts_in_unenumerated_pattern(name)
        result = self._apply_ranges(pattern, hpat)
        self._pattern_cache[pattern] = result
        return result

    def _enumeration_info(self, pattern):
        """
        returns (pattern, limits) taking a regular pattern and finding out
        which parts of it correspond to start/stop offsets.  limits is
        a tuple of (start, stop) or None
        """

        # Do not parse regexes for enumeration info
        if pattern.startswith('~'):
            return (pattern, None)

        # The regex used to match on the range, which can be [x] or [x-y].
        pattern_re = re.compile("^(.*)\[([-]?[0-9]+)(?:(?:-)([0-9]+))?\](.*)$")
        m = pattern_re.match(pattern)
        if m:
            (target, first, last, rest) = m.groups()
            first = int(first)
            if last:
                if first < 0:
                    raise errors.AnsibleError("invalid range: negative indices cannot be used as the first item in a range")
                last = int(last)
            else:
                last = first
            return (target, (first, last))
        else:
            return (pattern, None)

    def _apply_ranges(self, pat, hosts):
        """
        given a pattern like foo, that matches hosts, return all of hosts
        given a pattern like foo[0:5], where foo matches hosts, return the first 6 hosts
        """ 

        # If there are no hosts to select from, just return the
        # empty set. This prevents trying to do selections on an empty set.
        # issue#6258
        if not hosts:
            return hosts

        (loose_pattern, limits) = self._enumeration_info(pat)
        if not limits:
            return hosts

        (left, right) = limits

        if left == '':
            left = 0
        if right == '':
            right = 0
        left=int(left)
        right=int(right)
        try:
            if left != right:
                return hosts[left:right]
            else:
                return [ hosts[left] ]
        except IndexError:
            raise errors.AnsibleError("no hosts matching the pattern '%s' were found" % pat)

    def _create_implicit_localhost(self, pattern):
        # 如果pattern不在all礼拜中，则创建新的local Host，并添加到ungrouped组（没有则创建），然后返回Host对象
        new_host = Host(pattern)
        new_host.set_variable("ansible_python_interpreter", sys.executable)
        new_host.set_variable("ansible_connection", "local")
        ungrouped = self.get_group("ungrouped")
        if ungrouped is None:
            self.add_group(Group('ungrouped'))
            ungrouped = self.get_group('ungrouped')
            self.get_group('all').add_child_group(ungrouped)
        ungrouped.add_host(new_host)
        return new_host

    def _hosts_in_unenumerated_pattern(self, pattern):
        """ Get all host names matching the pattern """

        results = []
        hosts = []
        hostnames = set()

        # ignore any negative checks here, this is handled elsewhere
        pattern = pattern.replace("!","").replace("&", "")

        def __append_host_to_results(host):
            if host not in results and host.name not in hostnames:
                hostnames.add(host.name)
                results.append(host)

        groups = self.get_groups()
        for group in groups:
            if pattern == 'all':
                for host in group.get_hosts():
                    __append_host_to_results(host)
            else:
                if self._match(group.name, pattern):
                    for host in group.get_hosts():
                        __append_host_to_results(host)
                else:
                    matching_hosts = self._match_list(group.get_hosts(), 'name', pattern)
                    for host in matching_hosts:
                        __append_host_to_results(host)

        if pattern in ["localhost", "127.0.0.1"] and len(results) == 0:
            new_host = self._create_implicit_localhost(pattern)
            results.append(new_host)
        return results

    def clear_pattern_cache(self):
        ''' called exclusively by the add_host plugin to allow patterns to be recalculated '''
        self._pattern_cache = {}

    def groups_for_host(self, host):
        if host in self._hosts_cache:
            return self._hosts_cache[host].get_groups()
        else:
            return []

    def groups_list(self):
        if not self._groups_list:
            groups = {}
            for g in self.groups:
                groups[g.name] = [h.name for h in g.get_hosts()]
                ancestors = g.get_ancestors()
                for a in ancestors:
                    if a.name not in groups:
                        groups[a.name] = [h.name for h in a.get_hosts()]
            self._groups_list = groups
        return self._groups_list

    def get_groups(self):
        return self.groups

    def get_host(self, hostname):
        # 通过hostname获取主机对象，并缓存到self._hosts_cache字典中
        if hostname not in self._hosts_cache:
            self._hosts_cache[hostname] = self._get_host(hostname)
        return self._hosts_cache[hostname]

    def _get_host(self, hostname):
        # 根据hostname获取真实的主机对象
        if hostname in ['localhost','127.0.0.1']: # 如果hostname是本机地址
            for host in self.get_group('all').get_hosts(): # 查找all组中的所有hosts对象，进行name匹配
                if host.name in ['localhost', '127.0.0.1']:
                    return host
            return self._create_implicit_localhost(hostname) # 如果host不在all列表中，则创建新的host并返回
        else:
            for group in self.groups: # 分别遍历self.groups中的gruop列表，再依次遍历每个列表中的主机对象，进行name匹配
                for host in group.get_hosts():
                    if hostname == host.name: # 如果主机名相同，返回对应的主机对象
                        return host
        return None # 如果都没有找到则返回None

    def get_group(self, groupname):
        for group in self.groups:
            if group.name == groupname:
                return group
        return None

    def get_group_variables(self, groupname, update_cached=False, vault_password=None):
        if groupname not in self._vars_per_group or update_cached:
            self._vars_per_group[groupname] = self._get_group_variables(groupname, vault_password=vault_password)
        return self._vars_per_group[groupname]

    def _get_group_variables(self, groupname, vault_password=None):

        group = self.get_group(groupname)
        if group is None:
            raise errors.AnsibleError("group not found: %s" % groupname)

        vars = {}

        # plugin.get_group_vars retrieves just vars for specific group
        vars_results = [ plugin.get_group_vars(group, vault_password=vault_password) for plugin in self._vars_plugins if hasattr(plugin, 'get_group_vars')]
        for updated in vars_results:
            if updated is not None:
                vars = utils.combine_vars(vars, updated)

        # Read group_vars/ files
        vars = utils.combine_vars(vars, self.get_group_vars(group))

        return vars

    def get_variables(self, hostname, update_cached=False, vault_password=None):

        host = self.get_host(hostname)
        if not host:
            raise errors.AnsibleError("host not found: %s" % hostname)
        return host.get_variables()

    def get_host_variables(self, hostname, update_cached=False, vault_password=None):

        if hostname not in self._vars_per_host or update_cached:
            self._vars_per_host[hostname] = self._get_host_variables(hostname, vault_password=vault_password)
        return self._vars_per_host[hostname]

    def _get_host_variables(self, hostname, vault_password=None):

        host = self.get_host(hostname)
        if host is None:
            raise errors.AnsibleError("host not found: %s" % hostname)

        vars = {}

        # plugin.run retrieves all vars (also from groups) for host
        vars_results = [ plugin.run(host, vault_password=vault_password) for plugin in self._vars_plugins if hasattr(plugin, 'run')]
        for updated in vars_results:
            if updated is not None:
                vars = utils.combine_vars(vars, updated)

        # plugin.get_host_vars retrieves just vars for specific host
        vars_results = [ plugin.get_host_vars(host, vault_password=vault_password) for plugin in self._vars_plugins if hasattr(plugin, 'get_host_vars')]
        for updated in vars_results:
            if updated is not None:
                vars = utils.combine_vars(vars, updated)

        # still need to check InventoryParser per host vars
        # which actually means InventoryScript per host,
        # which is not performant
        if self.parser is not None:
            vars = utils.combine_vars(vars, self.parser.get_host_variables(host))

        # Read host_vars/ files
        vars = utils.combine_vars(vars, self.get_host_vars(host))

        return vars

    def add_group(self, group):
        if group.name not in self.groups_list():
            self.groups.append(group)
            self._groups_list = None  # invalidate internal cache 
        else:
            raise errors.AnsibleError("group already in inventory: %s" % group.name)

    def list_hosts(self, pattern="all"):

        """ return a list of hostnames for a pattern """
        # 列出符合pattern的主机列表

        result = [ h.name for h in self.get_hosts(pattern) ]
        if len(result) == 0 and pattern in ["localhost", "127.0.0.1"]: # pattern是本地控制机
            result = [pattern]
        return result

    def list_groups(self):
        return sorted([ g.name for g in self.groups ], key=lambda x: x)

    # TODO: remove this function
    def get_restriction(self):
        return self._restriction

    def restrict_to(self, restriction):
        """ 
        Restrict list operations to the hosts given in restriction.  This is used
        to exclude failed hosts in main playbook code, don't use this for other
        reasons.
        """
        if not isinstance(restriction, list):
            restriction = [ restriction ]
        self._restriction = restriction

    def also_restrict_to(self, restriction):
        """
        Works like restict_to but offers an additional restriction.  Playbooks use this
        to implement serial behavior.
        """
        if not isinstance(restriction, list):
            restriction = [ restriction ]
        self._also_restriction = restriction
    
    def subset(self, subset_pattern):
        """ 
        Limits inventory results to a subset of inventory that matches a given
        pattern, such as to select a given geographic of numeric slice amongst
        a previous 'hosts' selection that only select roles, or vice versa.  
        Corresponds to --limit parameter to ansible-playbook
        subset函数主要用来限制主机的数量，一般用于失败重试
        """        
        if subset_pattern is None:
            self._subset = None
        else:
            subset_pattern = subset_pattern.replace(',',':') # 多个subset之间通过逗号','或分号';'分割。
            subset_pattern = subset_pattern.replace(";",":").split(":")
            results = []
            # allow Unix style @filename data
            # 这里可以指定 --limit=@a.txt 样式的方式通过文件输入
            for x in subset_pattern:
                if x.startswith("@"):
                    fd = open(x[1:])
                    results.extend(fd.read().split("\n"))
                    fd.close()
                else:
                    results.append(x)
            self._subset = results

    def lift_restriction(self):
        """ Do not restrict list operations """
        self._restriction = None
    
    def lift_also_restriction(self):
        """ Clears the also restriction """
        self._also_restriction = None

    def is_file(self):
        """ did inventory come from a file? """
        if not isinstance(self.host_list, basestring):
            return False
        return os.path.exists(self.host_list)

    def basedir(self):
        """ if inventory came from a file, what's the directory? """
        # 如果inventory是一个文件，该函数用来获取其目录的绝对路径
        if not self.is_file():
            return None
        dname = os.path.dirname(self.host_list)
        if dname is None or dname == '' or dname == '.':
            cwd = os.getcwd()
            return os.path.abspath(cwd) 
        return os.path.abspath(dname)

    def src(self):
        """ if inventory came from a file, what's the directory and file name? """
        if not self.is_file():
            return None
        return self.host_list

    def playbook_basedir(self):
        """ returns the directory of the current playbook """
        return self._playbook_basedir

    def set_playbook_basedir(self, dir):
        """
        sets the base directory of the playbook so inventory can use it as a
        basedir for host_ and group_vars, and other things.
        """
        # Only update things if dir is a different playbook basedir
        if dir != self._playbook_basedir:
            self._playbook_basedir = dir
            # get group vars from group_vars/ files
            for group in self.groups:
                group.vars = utils.combine_vars(group.vars, self.get_group_vars(group, new_pb_basedir=True))
            # get host vars from host_vars/ files
            for host in self.get_hosts():
                host.vars = utils.combine_vars(host.vars, self.get_host_vars(host, new_pb_basedir=True))
            # invalidate cache
            self._vars_per_host = {}
            self._vars_per_group = {}

    def get_host_vars(self, host, new_pb_basedir=False):
        """ Read host_vars/ files """
        return self._get_hostgroup_vars(host=host, group=None, new_pb_basedir=new_pb_basedir)

    def get_group_vars(self, group, new_pb_basedir=False):
        """ Read group_vars/ files """
        return self._get_hostgroup_vars(host=None, group=group, new_pb_basedir=new_pb_basedir)

    def _get_hostgroup_vars(self, host=None, group=None, new_pb_basedir=False):
        """
        Loads variables from group_vars/<groupname> and host_vars/<hostname> in directories parallel
        to the inventory base directory or in the same directory as the playbook.  Variables in the playbook
        dir will win over the inventory dir if files are in both.
        """

        results = {}
        scan_pass = 0
        _basedir = self.basedir()

        # look in both the inventory base directory and the playbook base directory
        # unless we do an update for a new playbook base dir
        if not new_pb_basedir:
            basedirs = [_basedir, self._playbook_basedir]
        else:
            basedirs = [self._playbook_basedir]

        for basedir in basedirs:

            # this can happen from particular API usages, particularly if not run
            # from /usr/bin/ansible-playbook
            if basedir is None:
                continue

            scan_pass = scan_pass + 1

            # it's not an eror if the directory does not exist, keep moving
            if not os.path.exists(basedir):
                continue

            # save work of second scan if the directories are the same
            if _basedir == self._playbook_basedir and scan_pass != 1:
                continue

            if group and host is None:
                # load vars in dir/group_vars/name_of_group
                base_path = os.path.join(basedir, "group_vars/%s" % group.name)
                results = utils.load_vars(base_path, results, vault_password=self._vault_password)

            elif host and group is None:
                # same for hostvars in dir/host_vars/name_of_host
                base_path = os.path.join(basedir, "host_vars/%s" % host.name)
                results = utils.load_vars(base_path, results, vault_password=self._vault_password)

        # all done, results is a dictionary of variables for this particular host.
        return results

