# -*- coding: utf-8 -*-
# (c) 2013, Daniel Hokka Zakrisson <daniel@hozac.com>
# (c) 2014, Serge van Ginderachter <serge@vanginderachter.be>
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

import os
import ansible.constants as C
from ansible.inventory.host import Host
from ansible.inventory.group import Group
from ansible.inventory.ini import InventoryParser
from ansible.inventory.script import InventoryScript
from ansible import utils
from ansible import errors

class InventoryDirectory(object):
    ''' Host inventory parser for ansible using a directory of inventories. '''
    '''
        inventory为目录格式时，对inventory目录下inventory文件的解析类。
        目录格式的inventory，存在多文件相同Host，group的问题，因此十分复杂；
        涉及到多次合并和去重操作，后面的版本应该会考虑重构，这部分代码读起来很臃肿。
    '''

    def __init__(self, filename=C.DEFAULT_HOST_LIST):
        '''
        InventoryDirectory基类，包含以下属性：
        names，当前目录下的文件名称列表
        directory, 目录名
        parsers，解析器列表，每一个文件解析成一个parser
        hosts，主机对象字典
        groups，组对象字典
        '''

        # os.listdir函数用来列出当前目录下的所有文件
        self.names = os.listdir(filename)
        self.names.sort()
        self.directory = filename
        self.parsers = []
        self.hosts = {}
        self.groups = {}

        for i in self.names: # 变量该目录下所有文件

            # Skip files that end with certain extensions or characters
            # 跳过带有特定扩展名或字符结尾的文件
            if any(i.endswith(ext) for ext in ("~", ".orig", ".bak", ".ini", ".retry", ".pyc", ".pyo")):
                continue
            # Skip hidden files
            # 跳过隐藏文件
            if i.startswith('.') and not i.startswith('./'):
                continue
            # These are things inside of an inventory basedir
            # 跳过可能包含变量值的文件
            if i in ("host_vars", "group_vars", "vars_plugins"):
                continue
            fullpath = os.path.join(self.directory, i)
            if os.path.isdir(fullpath): # 如果该文件仍然是一个目录，则生成一个当前类对象的Parser（嵌套处理）
                parser = InventoryDirectory(filename=fullpath)
            elif utils.is_executable(fullpath): # 如果文件是一个可执行文件，则创建一个InventoryScript类的Parser
                parser = InventoryScript(filename=fullpath)
            else:
                parser = InventoryParser(filename=fullpath) # 如果文件是一个普通文件，则使用InventoryParser类的parser
            self.parsers.append(parser) # 将parser加入self.parsers的列表中

            # retrieve all groups and hosts form the parser and add them to
            # self, don't look at group lists yet, to avoid
            # recursion trouble, but just make sure all objects exist in self
            newgroups = parser.groups.values() # 获取当前parser的groups列表
            for group in newgroups:
                for host in group.hosts: # 遍历每个group对象的hosts对象列表，将host对象添加到self.hosts字典中。
                    self._add_host(host) # 添加host可能出现在多个文件返回结果中具有相同hostname的情况，需要进行合并。
            for group in newgroups: # 将groups列表中的group加到self.groups字典中，为什么不在上一个循环中一次性搞定。。。
                self._add_group(group) # 添加group，可能出现group重复的情况，需要进行合并

            # now check the objects lists so they contain only objects from
            # self; membership data in groups is already fine (except all &
            # ungrouped, see later), but might still reference objects not in self
            # 所有group都添加完成后还要做一些检测，包括子组，父组，host
            # 如果self.groups和self.hosts中的group对象和host对象出现名称相同，但对象不同的，都需要以self.groups和self.hosts中为准
            for group in self.groups.values():
                # iterate on a copy of the lists, as those lists get changed in
                # the loop
                # list with group's child group objects:
                for child in group.child_groups[:]: # a和a[:]有什么区别，干嘛多写3个字符
                    if child != self.groups[child.name]:
                        group.child_groups.remove(child)
                        group.child_groups.append(self.groups[child.name])
                # list with group's parent group objects:
                for parent in group.parent_groups[:]:
                    if parent != self.groups[parent.name]:
                        group.parent_groups.remove(parent)
                        group.parent_groups.append(self.groups[parent.name])
                # list with group's host objects:
                for host in group.hosts[:]:
                    if host != self.hosts[host.name]:
                        group.hosts.remove(host)
                        group.hosts.append(self.hosts[host.name])
                    # also check here that the group that contains host, is
                    # also contained in the host's group list
                    if group not in self.hosts[host.name].groups:
                        self.hosts[host.name].groups.append(group)

        # extra checks on special groups all and ungrouped
        # remove hosts from 'ungrouped' if they became member of other groups
        # 检测ungrouped组
        if 'ungrouped' in self.groups:
            ungrouped = self.groups['ungrouped']
            # loop on a copy of ungrouped hosts, as we want to change that list
            for host in ungrouped.hosts[:]:
                if len(host.groups) > 1:
                    host.groups.remove(ungrouped)
                    ungrouped.hosts.remove(host)

        # remove hosts from 'all' if they became member of other groups
        # all should only contain direct children, not grandchildren
        # direct children should have dept == 1
        # 对all组的检测
        if 'all' in self.groups:
            allgroup = self.groups['all' ]
            # loop on a copy of all's  child groups, as we want to change that list
            for group in allgroup.child_groups[:]: # 遍历allgroup中的所有group对象
                # groups might once have beeen added to all, and later be added
                # to another group: we need to remove the link wit all then
                if len(group.parent_groups) > 1 and allgroup in group.parent_groups:
                    # 如果当前group的父组数量大于1，并且allgroup是他的父组,则表名该组不是allgroup的子组
                    # all group的子组必须只有all一个父组
                    # real children of all have just 1 parent, all
                    # this one has more, so not a direct child of all anymore
                    group.parent_groups.remove(allgroup)
                    allgroup.child_groups.remove(group)
                elif allgroup not in group.parent_groups: # 以group的父组信息为准进行数据清理
                    # this group was once added to all, but doesn't list it as
                    # a parent any more; the info in the group is the correct
                    # info
                    allgroup.child_groups.remove(group)


    def _add_group(self, group):
        """ Merge an existing group or add a new one;
            Track parent and child groups, and hosts of the new one """

        if group.name not in self.groups: # 如果group不存在与self.groups中，则表示的新的group对象，so add it.
            # it's brand new, add him!
            self.groups[group.name] = group
        if self.groups[group.name] != group:
            # different object, merge
            # 可能在不同的文件中都定义该group对象，且有不同的属性，因此需要合并两个group对象。
            self._merge_groups(self.groups[group.name], group)

    def _add_host(self, host):
        if host.name not in self.hosts: # 如果host对象不存在与self.hosts字典中，则表示当前host对象为新的host对象，so add it.
            # Papa's got a brand new host
            self.hosts[host.name] = host
        if self.hosts[host.name] != host:
            # 如果在self.hosts字典中存在与当前host对象同名的对象，则合并两个对象。
            # 可能是在不同的文件中都定义了该host对象，且有不同的属性，因此需要合并两个host对象。
            # different object, merge
            self._merge_hosts(self.hosts[host.name], host)

    def _merge_groups(self, group, newgroup):
        """ Merge all of instance newgroup into group,
            update parent/child relationships
            group lists may still contain group objects that exist in self with
            same name, but was instanciated as a different object in some other
            inventory parser; these are handled later """

        # name
        if group.name != newgroup.name: # 待合并的group对象必须name相同
            raise errors.AnsibleError("Cannot merge group %s with %s" % (group.name, newgroup.name))

        # depth
        group.depth = max([group.depth, newgroup.depth]) # 获得group的真实深度

        # hosts list (host objects are by now already added to self.hosts)
        #  调用该函数之前已经调用了self_add_host(host)函数
        for host in newgroup.hosts: # 遍历所有新group的hosts对象列表
            grouphosts = dict([(h.name, h) for h in group.hosts]) # 创建旧group的host对象和对象名称组成的字典
            if host.name in grouphosts: # 判断新的group中的当前Host对象是否存在于旧group的Host对象列表中。
                # same host name but different object, merge
                self._merge_hosts(grouphosts[host.name], host) # 如果出现同名的Host，则需要进行合并
            else:
                # new membership, add host to group from self
                # group from self will also be added again to host.groups, but
                # as different object
                group.add_host(self.hosts[host.name]) # 将该Host对象添加到旧的group中
                # now remove this the old object for group in host.groups

                # 遍历当前Host对象的所有group，在上面的add_host操作中，host对象的groups列表中会出现相同的groups对象
                # 需要找到名称重复的group对象，并将其删除掉
                # Host对象的groups使用列表存储，使用append操作添加，可能出现相同名称的group对象存在的问题。
                # Group对象的hosts也使用同样的方式，为什么不使用字典操作？
                for hostgroup in [g for g in host.groups]:
                    if hostgroup.name == group.name and hostgroup != self.groups[group.name]:
                        self.hosts[host.name].groups.remove(hostgroup)


        # group child membership relation
        for newchild in newgroup.child_groups: # 遍历新的group的所有子group
            # dict with existing child groups:
            childgroups = dict([(g.name, g) for g in group.child_groups])
            # check if child of new group is already known as a child
            # 判断当前子group是否存在与旧的group的子group中
            if newchild.name not in childgroups: # 如果不存在则添加，否则忽略；子组不涉及真实的host对象操作，相对简单。
                self.groups[group.name].add_child_group(newchild)

        # group parent membership relation
        for newparent in newgroup.parent_groups: # 遍历所有的父组
            # dict with existing parent groups:
            parentgroups = dict([(g.name, g) for g in group.parent_groups])
            # check if parent of new group is already known as a parent
            if newparent.name not in parentgroups: # 判断父组是否重复，如果不重复，则添加新的父组，如果重复则将该组加为新父组的子组。。
                if newparent.name not in self.groups:
                    # group does not exist yet in self, import him
                    self.groups[newparent.name] = newparent
                # group now exists but not yet as a parent here
                self.groups[newparent.name].add_child_group(group)

        # variables
        group.vars = utils.combine_vars(group.vars, newgroup.vars) # 合并相同组名的两个Group对象的变量，以新的group对象的变量为准

    def _merge_hosts(self, host, newhost):
        """
        Merge all of instance newhost into host
        合并两个host对象。
        """

        # name
        if host.name != newhost.name: # 待合并的Host对象必须name相同
            raise errors.AnsibleError("Cannot merge host %s with %s" % (host.name, newhost.name))

        # group membership relation
        for newgroup in newhost.groups: # 遍历新的Host对象的所属groups列表
            # dict with existing groups:
            hostgroups = dict([(g.name, g) for g in host.groups]) # 旧的Host对象的group列表的字典。
            # check if new group is already known as a group
            # 检测新的Host的当前group是否存在与旧的Host的groups列表中
            if newgroup.name not in hostgroups: # 如果存在则忽略，否则进行下一步处理
                if newgroup.name not in self.groups: # 如果当前group不存在与self.groups字典中，则添加到self.groups字典
                    # group does not exist yet in self, import him
                    self.groups[newgroup.name] = newgroup
                # group now exists but doesn't have host yet
                self.groups[newgroup.name].add_host(host) # 将当前Host对象添加到该Group对象中。

        # variables
        # 合并新旧两个Host对象的变量，以新对象为准，也就是说排序在后面的文件中的Host变量优先！
        host.vars = utils.combine_vars(host.vars, newhost.vars)

    def get_host_variables(self, host):
        """ Gets additional host variables from all inventories """
        # 遍历所有的parser，合并所有的变量到vars字典中
        vars = {}
        for i in self.parsers:
            vars.update(i.get_host_variables(host))
        return vars

