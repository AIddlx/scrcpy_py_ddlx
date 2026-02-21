"""
接口契约验证测试

运行方式：
    python tests_gui/test_interface_contracts.py

目的：
    验证所有组件是否遵守接口契约，确保重构不会破坏兼容性

重要：
    - 此测试检查的是【公共接口】，不是内部实现
    - 重构后运行此测试，如果通过则说明接口兼容性保持
    - 如果失败，说明接口发生了破坏性变化

变更记录：
    2026-02-21: 初始版本
"""

import sys
import inspect
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# ============================================================
# 核心接口定义 - 这些接口在重构中必须保持不变
# ============================================================

REQUIRED_INTERFACES = {
    'OpenGLVideoWidget': {
        'module': 'scrcpy_py_ddlx.core.player.video.opengl_widget',
        'description': 'OpenGL 视频渲染组件',
        'methods': [
            # 核心渲染接口
            ('set_delay_buffer', '设置帧缓冲区'),
            ('set_control_queue', '设置控制消息队列'),
            ('set_consume_callback', '设置帧消费回调'),
            ('set_frame_size_changed_callback', '设置尺寸变化回调'),
            ('set_nv12_mode', '设置 NV12 渲染模式'),
            ('is_nv12_supported', '检查 NV12 支持'),
            # 设备信息
            ('set_device_size', '设置设备尺寸'),
        ],
        'expected_bases': ['QOpenGLWidget', 'InputHandler', 'CoordinateMapper'],
    },
    'VideoWindow': {
        'module': 'scrcpy_py_ddlx.core.player.video.video_window',
        'description': '视频窗口（CPU 渲染）',
        'methods': [
            ('set_device_info', '设置设备信息'),
            ('update_frame', '更新帧'),
            ('set_control_queue', '设置控制队列'),
            ('set_delay_buffer', '设置帧缓冲'),
        ],
        'properties': ['video_widget'],
    },
    'OpenGLVideoWindow': {
        'module': 'scrcpy_py_ddlx.core.player.video.video_window',
        'description': '视频窗口（OpenGL 渲染）',
        'methods': [
            ('set_device_info', '设置设备信息'),
            ('update_frame', '更新帧'),
            ('set_control_queue', '设置控制队列'),
            ('set_delay_buffer', '设置帧缓冲'),
        ],
        'properties': ['video_widget'],
    },
    'DelayBuffer': {
        'module': 'scrcpy_py_ddlx.core.decoder.delay_buffer',
        'description': '单帧缓冲区',
        'methods': [
            ('consume', '消费帧（线程安全）'),
            ('push', '推入帧（线程安全）'),
        ],
    },
    'InputHandler': {
        'module': 'scrcpy_py_ddlx.core.player.video.input_handler',
        'description': '输入处理 Mixin',
        'methods': [
            ('set_control_queue', '设置控制队列'),
            ('set_device_size', '设置设备尺寸'),
        ],
    },
    'CoordinateMapper': {
        'module': 'scrcpy_py_ddlx.core.player.video.input_handler',
        'description': '坐标映射 Mixin',
        'methods': [],  # CoordinateMapper 的方法可能变化，不强制检查
    },
}


def check_interface(class_name: str, config: dict) -> tuple[bool, list[str]]:
    """检查类是否实现了所有必需的接口"""
    errors = []

    try:
        module = __import__(config['module'], fromlist=[class_name])
        cls = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        return False, [f"Cannot import {class_name}: {e}"]

    # 检查方法
    for method_info in config.get('methods', []):
        if isinstance(method_info, tuple):
            method_name, description = method_info
        else:
            method_name = method_info
            description = ''

        if not hasattr(cls, method_name):
            errors.append(f"Missing method: {class_name}.{method_name} ({description})")
        else:
            attr = getattr(cls, method_name)
            if not callable(attr):
                errors.append(f"{class_name}.{method_name} is not callable")

    # 检查属性
    for prop_name in config.get('properties', []):
        if not hasattr(cls, prop_name):
            errors.append(f"Missing property: {class_name}.{prop_name}")

    return len(errors) == 0, errors


def check_inheritance(class_name: str, config: dict) -> tuple[bool, list[str]]:
    """检查继承关系"""
    errors = []

    if 'expected_bases' not in config:
        return True, []

    try:
        module = __import__(config['module'], fromlist=[class_name])
        cls = getattr(module, class_name)

        actual_bases = [base.__name__ for base in cls.__bases__]
        expected = config['expected_bases']

        for expected_base in expected:
            if expected_base not in actual_bases:
                errors.append(f"{class_name} should inherit from {expected_base}, actual bases: {actual_bases}")

    except Exception as e:
        errors.append(f"Cannot check inheritance for {class_name}: {e}")

    return len(errors) == 0, errors


def check_delay_buffer_thread_safety() -> tuple[bool, str]:
    """检查 DelayBuffer 是否线程安全"""
    try:
        from scrcpy_py_ddlx.core.decoder.delay_buffer import DelayBuffer
        import threading
        import time

        buffer = DelayBuffer()

        results = {'push': 0, 'consume': 0, 'errors': []}

        class MockFrame:
            def __init__(self, idx):
                self.idx = idx
                self.data = f'frame_{idx}'.encode()

        def push_thread():
            try:
                for i in range(50):
                    buffer.push(MockFrame(i))
                    results['push'] += 1
                    time.sleep(0.002)
            except Exception as e:
                results['errors'].append(f"push error: {e}")

        def consume_thread():
            try:
                for i in range(50):
                    frame = buffer.consume()
                    if frame:
                        results['consume'] += 1
                    time.sleep(0.002)
            except Exception as e:
                results['errors'].append(f"consume error: {e}")

        t1 = threading.Thread(target=push_thread)
        t2 = threading.Thread(target=consume_thread)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        if results['errors']:
            return False, f"Thread safety errors: {results['errors']}"

        if results['push'] != 50:
            return False, f"Push count mismatch: expected 50, got {results['push']}"

        return True, f"Thread safety OK (pushed={results['push']}, consumed={results['consume']})"

    except Exception as e:
        return False, f"Thread safety test error: {e}"


def generate_interface_report() -> dict:
    """生成接口快照，用于重构前后对比"""
    report = {}

    for class_name, config in REQUIRED_INTERFACES.items():
        try:
            module = __import__(config['module'], fromlist=[class_name])
            cls = getattr(module, class_name)

            # 获取所有公共方法签名
            methods = {}
            for name in dir(cls):
                if name.startswith('_') and not name.startswith('__'):
                    continue
                attr = getattr(cls, name)
                if callable(attr):
                    try:
                        sig = inspect.signature(attr)
                        methods[name] = str(sig)
                    except:
                        methods[name] = '(signature unavailable)'

            # 获取所有 property
            properties = []
            for name in dir(cls):
                attr = getattr(cls, name, None)
                if isinstance(attr, property):
                    properties.append(name)

            report[class_name] = {
                'methods': methods,
                'properties': properties,
                'bases': [base.__name__ for base in cls.__bases__],
            }

        except Exception as e:
            report[class_name] = {'error': str(e)}

    return report


def main():
    print("=" * 60)
    print("Interface Contract Validation Test")
    print("=" * 60)
    print()

    all_passed = True
    results = []

    # 1. 检查接口存在性
    print("1. Checking interface existence")
    print("-" * 40)
    for class_name, config in REQUIRED_INTERFACES.items():
        passed, errors = check_interface(class_name, config)
        status = "[PASS]" if passed else "[FAIL]"
        desc = config.get('description', '')
        print(f"  {class_name}: {status} ({desc})")
        for error in errors:
            print(f"    - {error}")
        if not passed:
            all_passed = False
        results.append((class_name, 'interface', passed))
    print()

    # 2. 检查继承关系
    print("2. Checking inheritance")
    print("-" * 40)
    for class_name, config in REQUIRED_INTERFACES.items():
        passed, errors = check_inheritance(class_name, config)
        if config.get('expected_bases'):
            status = "[PASS]" if passed else "[FAIL]"
            print(f"  {class_name}: {status}")
            for error in errors:
                print(f"    - {error}")
            if not passed:
                all_passed = False
            results.append((class_name, 'inheritance', passed))
    print()

    # 3. 检查线程安全
    print("3. Checking DelayBuffer thread safety")
    print("-" * 40)
    passed, message = check_delay_buffer_thread_safety()
    print(f"  {'[PASS]' if passed else '[FAIL]'} {message}")
    if not passed:
        all_passed = False
    print()

    # 4. 生成接口快照
    print("4. Interface Snapshot (for comparison)")
    print("-" * 40)
    report = generate_interface_report()
    for class_name, info in report.items():
        if 'error' in info:
            print(f"  {class_name}: ERROR - {info['error']}")
        else:
            print(f"  {class_name}:")
            print(f"    Bases: {info['bases']}")
            if info['properties']:
                print(f"    Properties: {info['properties']}")
            # 打印关键方法签名
            key_methods = [m for m in ['set_delay_buffer', 'set_control_queue', 'consume', 'push'] if m in info['methods']]
            if key_methods:
                print(f"    Key methods: {key_methods}")
    print()

    # 总结
    print("=" * 60)
    if all_passed:
        print("All checks passed [PASS]")
        print("Safe to proceed with refactoring.")
        print("After refactoring, run this test again to verify compatibility.")
    else:
        print("Some checks failed [FAIL]")
        print("Review and fix issues before refactoring.")
    print("=" * 60)

    # 打印结果摘要
    print()
    print("Summary:")
    for name, check_type, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name} ({check_type})")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
