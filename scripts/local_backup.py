"""
本地备份脚本

功能：
1. 创建 git tag 保存当前状态
2. 创建本地备份压缩包
3. 不推送到远程仓库

使用方式：
    python scripts/local_backup.py [tag_name]

示例：
    python scripts/local_backup.py before_qopenglwindow_refactor
"""

import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime


def run_cmd(cmd, cwd=None):
    """运行命令"""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr


def create_git_tag(tag_name, project_root):
    """创建 git tag"""
    print(f"创建 git tag: {tag_name}")

    # 检查是否有未提交的更改
    code, out, err = run_cmd("git status --porcelain", cwd=project_root)
    if out.strip():
        print("警告: 有未提交的更改，建议先提交")
        print(out[:500])

    # 创建 tag
    code, out, err = run_cmd(f'git tag -a "{tag_name}" -m "Backup: {tag_name}"', cwd=project_root)
    if code != 0:
        if "already exists" in err:
            print(f"  Tag {tag_name} 已存在，跳过创建")
        else:
            print(f"  创建 tag 失败: {err}")
            return False
    else:
        print(f"  ✓ Tag {tag_name} 创建成功")

    # 显示所有 tag
    code, out, err = run_cmd("git tag -l", cwd=project_root)
    print(f"\n当前所有 tags:")
    for line in out.strip().split('\n')[-10:]:
        print(f"  - {line}")

    return True


def create_backup_archive(project_root, tag_name):
    """创建备份压缩包"""
    backup_dir = project_root.parent / f"{project_root.name}_backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{project_root.name}_{tag_name}_{timestamp}"

    print(f"\n创建备份: {backup_dir / archive_name}")

    # 使用 git archive 创建压缩包
    archive_path = backup_dir / f"{archive_name}.zip"
    code, out, err = run_cmd(
        f'git archive --format=zip --output="{archive_path}" HEAD',
        cwd=project_root
    )

    if code == 0:
        print(f"  ✓ 备份创建成功: {archive_path}")
        print(f"  大小: {archive_path.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print(f"  ✗ 备份创建失败: {err}")

    return archive_path


def main():
    project_root = Path(__file__).parent.parent

    # 确定tag名称
    if len(sys.argv) > 1:
        tag_name = sys.argv[1]
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag_name = f"backup_{timestamp}"

    print("=" * 60)
    print("本地备份工具")
    print("=" * 60)
    print(f"项目目录: {project_root}")
    print(f"Tag 名称: {tag_name}")
    print()

    # 创建 git tag
    create_git_tag(tag_name, project_root)

    # 创建备份压缩包
    create_backup_archive(project_root, tag_name)

    print()
    print("=" * 60)
    print("完成！")
    print("=" * 60)
    print("""
注意事项：
1. Tag 只存在于本地，不会推送到远程
2. 备份压缩包包含所有已提交的文件
3. 未提交的更改不会包含在备份中

恢复方法：
  git checkout <tag_name>        # 查看备份时的代码
  git checkout main              # 返回主分支

查看所有 tag：
  git tag -l

删除 tag：
  git tag -d <tag_name>
""")


if __name__ == "__main__":
    main()
