# coding: utf8

import codecs
import os
import time

import sublime
import sublime_plugin


class ViewInfo(object):
    def __init__(self):
        self.need_process = False
        self.converted = False
        self.encoding = 'UTF-8'
        self.is_converting = False
        self.revert_command_on_modified_count = 0
        self.prevent_reload = False
        self.last_save_time = None


class ViewInfoList(object):
    def __init__(self):
        self.view_info_dict = {}  # {buffer_id : ViewInfo}

    def get(self, view):
        view_info = self.view_info_dict.get(view.id())
        if not view_info:
            view_info = ViewInfo()
            self.view_info_dict[view.id()] = view_info

            if view.file_name():
                _, ext = os.path.splitext(view.file_name())
                view_info.need_process = True  # ext.lower() in ['.txt', '.md', '.bat', '.cmd']

        return view_info

    def remove(self, view):
        try:
            del self.view_info_dict[view.id()]
        except:
            pass


view_infos = ViewInfoList()


class EventListener(sublime_plugin.EventListener):
    def __init__(self):
        self.first_view = None
        self.has_checked_first_view = False
        pass

    def on_load(self, view):
        view_info = view_infos.get(view)
        if not view_info.need_process:
            return

        self.to_utf8_view(view)

    def on_modified(self, view):
        view_info = view_infos.get(view)
        if not view_info.need_process:
            return

        # print('[SublimeGbkEncoding] on_modified: %s, %s, is_scratch: %s' % (view.command_history(0), view.command_history(1), view.is_scratch()))

        if view_info.converted:
            command = view.command_history(0)
            command1 = view.command_history(1)
            none_command = ('', None, 0)

            # 回滚undo convert_to_utf8
            if command == none_command:
                if command1[0] == 'convert_to_utf8':
                    view.set_scratch(True)
                    view_info.is_converting = True
                    view.run_command('redo')
                    view_info.is_converting = False
            # 回滚reload
            elif command[0] == 'revert':
                if view_info.converted:
                    # revert命令会调用两次on_modified, 第二次之后才做处理。
                    if view_info.revert_command_on_modified_count > 0:
                        sublime.set_timeout(lambda: self.process_revert(view), 0)
                        view_info.revert_command_on_modified_count = 0
                    else:
                        view_info.revert_command_on_modified_count += 1

        if view.is_scratch() and not view_info.is_converting:
            view.set_scratch(False)

    def process_revert(self, view):
        view_info = view_infos.get(view)
        if not view_info.need_process:
            return

        # 如果reload是自己存盘造成的，直接undo这个reload操作。
        if view_info.prevent_reload and view_info.last_save_time == os.path.getmtime(view.file_name()):
            view.set_scratch(True)
            view_info.is_converting = True

            view.run_command('undo')

            view.set_encoding('UTF-8')

            # 确保在undo命令完成后才做清理
            sublime.set_timeout(lambda: after_undo_revert(view), 0)
        # reload确实是外部改动造成的，需要再次转换编码。
        else:
            view.set_scratch(True)
            view_info.is_converting = True
            view.run_command('convert_to_utf8', {'encoding': view_info.encoding})
            view_info.is_converting = False

    # post_save事件不使用异步处理，因为在close时，异步处理无法保证数据被存盘。
    def on_post_save(self, view):
        view_info = view_infos.get(view)
        if not view_info.need_process:
            return

        if view_info.converted:
            self.save_with_encoding(view, view_info.encoding)
            view_info.prevent_reload = True
            view_info.last_save_time = os.path.getmtime(view.file_name())

    # close事件不使用异步处理，因为行为不稳定。
    def on_close(self, view):
        view_infos.remove(view)

    def on_activated(self, view):
        view_info = view_infos.get(view)
        if not view_info.need_process:
            return

        self.to_utf8_view(view)  # Sublime 3 启动时不能正确调用on_load事件，只好在这里加上。

    def to_utf8_view(self, view):
        view_info = view_infos.get(view)

        if view_info.converted:
            return
        if not view.file_name():
            return

        if view.encoding() == 'Hexadecimal':
            view_info.need_process = False
            return

        begin_clock = time.perf_counter()

        encoding = 'gbk'

        # 有BOM，不需要处理。
        if os.path.getsize(view.file_name()) >= 4:
            with open(view.file_name(), 'rb') as fp:
                header = fp.read(4)
                for bom in [codecs.BOM_UTF8, codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE, codecs.BOM_UTF32_BE,
                            codecs.BOM_UTF32_LE]:
                    if header.startswith(bom):
                        print('[SublimeGbkEncoding] BOM detected.')
                        encoding = None

        # ASCII文件，不需要处理。
        if encoding:
            try:
                with codecs.open(view.file_name(), 'rb', 'ascii') as fp:
                    fp.read()
                print('[SublimeGbkEncoding] ASCII file detected.')
                encoding = None
            except UnicodeDecodeError:
                pass

        # 非GBK编码文件，也不需要处理。
        if encoding:
            try:
                with codecs.open(view.file_name(), 'rb', encoding) as fp:
                    fp.read()
                print('[SublimeGbkEncoding] %s file detected.' % encoding.upper())
            except UnicodeDecodeError:
                print('[SublimeGbkEncoding] Non-%s file detected.' % encoding.upper())
                encoding = None

        end_clock = time.perf_counter()
        consume_time = end_clock - begin_clock
        print('[SublimeGbkEncoding] Detect encoding %s using %fs.' % (encoding, consume_time))
        if consume_time > 1:
            print('[SublimeGbkEncoding] Cancel encoding conversion since it consume too much time.')
            encoding = None

        if encoding:
            view.set_scratch(True)
            view_info.is_converting = True
            view.run_command('convert_to_utf8', {'encoding': encoding})
            view_info.is_converting = False
            view_info.encoding = encoding

            # 尝试解决打开第一个文档时，run_command调用不成功的问题。
            # build 3126似乎无此问题了，暂时屏蔽。
            self.has_checked_first_view = True
            if not self.has_checked_first_view:
                print('[SublimeGbkEncoding] Register recheck callback in 500ms.')
                self.first_view = view
                sublime.set_timeout(self.recheck_encoding, 500)
                self.has_checked_first_view = True
        else:
            view_info.need_process = False
            print('[SublimeGbkEncoding] Skip encoding conversion.')

    def recheck_encoding(self):
        assert self.first_view
        print('[SublimeGbkEncoding] Recheck encoding.')
        self.to_utf8_view(self.first_view)

    def save_with_encoding(self, view, encoding):
        file_name = view.file_name()
        reg_all = sublime.Region(0, view.size())
        text = view.substr(reg_all).replace('\n', '\r\n').encode(encoding)

        with open(file_name, 'wb') as fp:
            fp.write(text)


def after_undo_revert(view):
    view_info = view_infos.get(view)
    if not view_info.need_process:
        return

    if view_info.prevent_reload:
        view_info.is_converting = False
        view_info.prevent_reload = False


class ConvertToUtf8Command(sublime_plugin.TextCommand):
    def run(self, edit, encoding=None):
        print('[SublimeGbkEncoding] ConvertToUtf8Command')
        view = self.view
        if not encoding:
            return

        file_name = view.file_name()
        if not (file_name and os.path.exists(file_name)):
            return

        print('[SublimeGbkEncoding] Converting from %s to UTF-8...' % encoding)
        begin_clock = time.perf_counter()

        try:
            with codecs.open(file_name, 'rb', encoding) as fp:
                content = fp.read()
        except LookupError:
            sublime.error_message('[SublimeGbkEncoding] Encoding {0} is not supported.'.format(encoding))
            return
        except UnicodeDecodeError:
            sublime.error_message(
                '[SublimeGbkEncoding] Errors occurred while converting {0} with {1} encoding'.format(
                    os.path.basename(file_name), encoding))
            return

        # 去掉\r，否则会在编辑器中显示成“CR”
        content = content.replace('\r\n', '\n')
        regions = sublime.Region(0, view.size())
        sel = view.sel()
        rs = [x for x in sel]
        vp = view.viewport_position()
        view.set_viewport_position(tuple([0, 0]))
        view.replace(edit, regions, content)
        sel.clear()
        for x in rs:
            sel.add(sublime.Region(x.a, x.b))
        view.set_viewport_position(vp)

        view.set_encoding('utf-8')

        end_clock = time.perf_counter()
        print('[SublimeGbkEncoding] Converted using %f s.' % (end_clock - begin_clock))

        view_info = view_infos.get(view)
        view_info.converted = True

        sublime.status_message('{0} -> UTF8'.format(encoding))

    def is_enabled(self):
        return self.view.encoding() != 'Hexadecimal'
