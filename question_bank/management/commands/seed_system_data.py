from django.core.management.base import BaseCommand

from question_bank.models import Section, Subject, Tag


SYSTEM_SUBJECTS = {
    "数学分析": ["极限与连续", "一元函数微分学", "一元函数积分学", "级数"],
    "高等代数": ["多项式", "行列式", "矩阵", "线性方程组", "线性空间"],
}

ERROR_TYPES = ["概念不清", "计算错误", "方法选择错误", "证明不完整"]


class Command(BaseCommand):
    help = "幂等创建题库的科目、常用章节和错误类型标签。"

    def handle(self, *args, **options):
        subject_count = section_count = tag_count = 0
        for subject_name, section_names in SYSTEM_SUBJECTS.items():
            subject, created = Subject.objects.get_or_create(name=subject_name)
            subject_count += int(created)
            for sort_order, section_name in enumerate(section_names):
                _, created = Section.objects.get_or_create(
                    subject=subject,
                    name=section_name,
                    defaults={"sort_order": sort_order},
                )
                section_count += int(created)

        for tag_name in ERROR_TYPES:
            _, created = Tag.objects.get_or_create(
                name=tag_name,
                parent=None,
                defaults={"kind": "error_type"},
            )
            tag_count += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"系统数据已就绪：新增科目 {subject_count} 个，章节 {section_count} 个，错误类型 {tag_count} 个。"
            )
        )
