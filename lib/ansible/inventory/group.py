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

class Group(object):
    ''' a group of ansible hosts '''
    ''' inventory Group 基类 '''

    # 使用__slots__ 限制类属性的访问
    __slots__ = [ 'name', 'hosts', 'vars', 'child_groups', 'parent_groups', 'depth', '_hosts_cache' ]

    def __init__(self, name=None):
        '''
            Group基类，包含一下属性：
            depth，Group深度，添加子group的时候会进行深度探测
            name，组名
            hosts，该组下的host对象列表
            vars，该组的组变量
            child_groups，子组，组允许嵌套，子组也允许有子组，在添加子组的时候要同时在父组内添加子组，并在子组内添加父组。
            parent_groups，父组，既该组是哪一组的子组，在添加子组的时候要同时在父组内添加子组，并在子组内添加父组。
            _host_cache，用来缓存host数据
        '''

        self.depth = 0
        self.name = name
        self.hosts = []
        self.vars = {}
        self.child_groups = []
        self.parent_groups = []
        self._hosts_cache = None
        #self.clear_hosts_cache()
        if self.name is None:
            raise Exception("group name is required")

    def add_child_group(self, group):
        '''
            添加子group到当前group，同时探测子组的深度，修改当前group的深度，并在子组中将该组添加成父组。
        '''

        if self == group:
            raise Exception("can't add group to itself")

        # don't add if it's already there
        if not group in self.child_groups:
            self.child_groups.append(group)

            # update the depth of the child
            group.depth = max([self.depth+1, group.depth])

            # update the depth of the grandchildren
            group._check_children_depth()

            # now add self to child's parent_groups list, but only if there
            # isn't already a group with the same name
            # 在本组中添加完成子组后，需要在子组中添加该组为父组。
            if not self.name in [g.name for g in group.parent_groups]:
                group.parent_groups.append(self)

            self.clear_hosts_cache() # 清理缓存

    def _check_children_depth(self):

        for group in self.child_groups:
            group.depth = max([self.depth+1, group.depth])
            group._check_children_depth()

    def add_host(self, host):
        ''' 添加新的host对象到该group，同时在该host对象中设置该host属于哪个组，一个host可能通过子组的方式属于多个组。'''

        self.hosts.append(host)
        host.add_group(self)
        self.clear_hosts_cache()

    def set_variable(self, key, value):
        # 设置变量

        self.vars[key] = value

    def clear_hosts_cache(self):
        # 清理host缓存，同时清理父组的host缓存，以保证子组在host变更的同时，父组也会变更。

        self._hosts_cache = None
        for g in self.parent_groups:
            g.clear_hosts_cache()

    def get_hosts(self):
        # 获取host对象，同时进行缓存

        if self._hosts_cache is None:
            self._hosts_cache = self._get_hosts()

        return self._hosts_cache

    def _get_hosts(self):
        # 返回当前group的所有host对象列表，去重

        hosts = [] # hosts为最终返回的host对象列表
        seen = {} # seen用来判断是否重复处理
        for kid in self.child_groups:
            kid_hosts = kid.get_hosts()
            for kk in kid_hosts:
                if kk not in seen:
                    seen[kk] = 1
                    hosts.append(kk)
        for mine in self.hosts:
            if mine not in seen:
                seen[mine] = 1
                hosts.append(mine)
        return hosts

    def get_variables(self):
        # 获取变量的副本
        return self.vars.copy()

    def _get_ancestors(self):
        # 嵌套获取所有父group对象
        results = {}
        for g in self.parent_groups:
            results[g.name] = g
            results.update(g._get_ancestors())
        return results

    def get_ancestors(self):
        # 获取所有父group对象

        return self._get_ancestors().values()

