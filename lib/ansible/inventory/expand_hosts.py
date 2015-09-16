# -*- coding: utf-8 -*-
# (c) 2012, Zettar Inc.
# Written by Chin Fang <fangchin@zettar.com>
#
# This file is part of Ansible
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#

'''
This module is for enhancing ansible's inventory parsing capability such
that it can deal with hostnames specified using a simple pattern in the
form of [beg:end], example: [1:5], [a:c], [D:G]. If beg is not specified,
it defaults to 0.

If beg is given and is left-zero-padded, e.g. '001', it is taken as a
formatting hint when the range is expanded. e.g. [001:010] is to be
expanded into 001, 002 ...009, 010.

Note that when beg is specified with left zero padding, then the length of
end must be the same as that of beg, else an exception is raised.
'''
import string

from ansible import errors

def detect_range(line = None):
    '''
    A helper function that checks a given host line to see if it contains
    a range pattern described in the docstring above.

    Returnes True if the given line contains a pattern, else False.
    '''
    # 该函数是用来检测注入的line是否包含范围pattern
    # 如果包含[:],且顺序正确，则返回True，表示是一组host
    if 0 <= line.find("[") < line.find(":") < line.find("]"):
        return True
    else:
        return False

def expand_hostname_range(line = None):
    '''
    A helper function that expands a given line that contains a pattern
    specified in top docstring, and returns a list that consists of the
    expanded version.

    The '[' and ']' characters are used to maintain the pseudo-code
    appearance. They are replaced in this function with '|' to ease
    string splitting.

    References: http://ansible.github.com/patterns.html#hosts-and-groups
    '''
    all_hosts = []
    if line:
        # A hostname such as db[1:6]-node is considered to consists
        # three parts:
        # head: 'db'
        # nrange: [1:6]; range() is a built-in. Can't use the name
        # tail: '-node'

        # Add support for multiple ranges in a host so:
        # db[01:10:3]node-[01:10]
        # - to do this we split off at the first [...] set, getting the list
        #   of hosts and then repeat until none left.
        # - also add an optional third parameter which contains the step. (Default: 1)
        #   so range can be [01:10:2] -> 01 03 05 07 09
        # FIXME: make this work for alphabetic sequences too.
        # 貌似当前版本并不支持字母顺序表示法

        # 将输入的hostname解析成3段，head(头部)，nrange(范围)，tail(尾部)
        # 这里使用了一个小技巧，先把中括号缓存"|"，然后使用split函数
        # nrange和python的range相同语法，支持step，既可以[1:6]，也可以[1:6:2]
        # [1:6]表示[1,2,3,4,5,6]；[1:6:2]表示[1,3,5]
        (head, nrange, tail) = line.replace('[','|',1).replace(']','|',1).split('|')
        bounds = nrange.split(":")
        if len(bounds) != 2 and len(bounds) != 3:
            raise errors.AnsibleError("host range incorrectly specified")
        beg = bounds[0] # 获取范围起始标识
        end = bounds[1]
        if len(bounds) == 2:
            step = 1 # 设置范围step
        else:
            step = bounds[2]
        if not beg:
            beg = "0" # 如果未输入起始标识，则默认使用'0'为起始标识，既[:6] == [0:6]
        if not end:
            raise errors.AnsibleError("host range end value missing") # 不允许结束标识为空
        if beg[0] == '0' and len(beg) > 1:
            # 如果起始标识为0开头，则需要起始标识与结束标识长度相同，既[01:06]，这种方式使用0占位。
            rlen = len(beg) # range length formatting hint
            if rlen != len(end):
                raise errors.AnsibleError("host range format incorrectly specified!")
            fill = lambda _: str(_).zfill(rlen)  # range sequence 使用0填充，这里使用了lambda函数
        else:
            fill = str # 如果需要用0填充则使用 lambda _: str(_).zfill(rlen) 函数，否则只使用str函数。

        try:
            # 如果beg和end是字母的形式，则通过这种方式获取seq
            i_beg = string.ascii_letters.index(beg)
            i_end = string.ascii_letters.index(end)
            if i_beg > i_end:
                raise errors.AnsibleError("host range format incorrectly specified!")
            seq = string.ascii_letters[i_beg:i_end+1]
        except ValueError:  # not an alpha range
            # 当beg和end是数字形式的时候，这里好像有个bug，如果beg为0，end为f，会怎样？
            # 果然会触发一个bug，ValueError: invalid literal for int() with base 10: 'a'
            # TODO:这里有一个bug，需要修复一下，将这个异常转换为Error
            seq = range(int(beg), int(end)+1, int(step))

        # 将下面这段代码的单引号去掉，用来捕获上面的语句出现异常的情况
        '''
        except Exception,e:
            raise errors.AnsibleError("host range format incorrectly specified! %s." %e)
        '''

        # seq 为一个可迭代对象，要么是string.ascii_letters中的一部分，要么是range()范围
        for rseq in seq:
            hname = ''.join((head, fill(rseq), tail)) # 头部，中部，尾部组成新的hostname

            # 这里还有一个嵌套调用，如果新的hostname仍然包含范围pattern，则继续迭代；否则加到all_host列表
            if detect_range(hname):
                all_hosts.extend( expand_hostname_range( hname ) )
            else:
                all_hosts.append(hname)

        return all_hosts
