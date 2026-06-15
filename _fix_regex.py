"""临时脚本：修复 phase4_feature_extractor.py 中的正则"""
import re

TARGET = 'phase4_feature_extractor.py'

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# 定位 extract_keywords_from_criteria 函数
pattern = r'def extract_keywords_from_criteria\(criteria: str\) -> List\[str\]:.*?(?=\ndef has_keywords)'
match = re.search(pattern, content, re.DOTALL)
if not match:
    print('ERROR: function not found')
    exit(1)

old_func = match.group(0)
print('=== OLD (first 200 chars) ===')
print(old_func[:200])
print('...')

# 新版本：用 \u 转义明确写出中文引号，避免字体混淆
new_func = r'''def extract_keywords_from_criteria(criteria: str) -> List[str]:
    """
    从 judgment_criteria 文本中启发式提取关键词。
    策略：找引号内的词、顿号分隔列表、以及明确否定/肯定的判定词。
    这是通用逻辑，不依赖任何业务场景。
    """
    keywords = []
    # 提取引号内词汇（覆盖 ASCII " ' + 中文左右引号）
    # 使用 \\u 转义明确写出码点，避免编辑器字体混淆
    QUOTE = r'["“”'‘’]'
    NOT_QUOTE = r'[^"“”'‘’]'
    quoted = re.findall(QUOTE + r'(' + NOT_QUOTE + r'+)' + QUOTE, criteria)
    keywords.extend(quoted)
    # 提取顿号/逗号分隔的判定词列表
    lists = re.findall(
        QUOTE + r'(' + NOT_QUOTE + r'+)' + QUOTE
        + r'(?:[，、](?:等|词|字眼|词汇))?'
        + r'|([一-龥]{2,6})[，、](?:等|词|字眼|词汇)',
        criteria)
    for m in lists:
        for group in m:
            if group and len(group) >= 2:
                keywords.append(group.strip())
    # 去重
    return list(set([k.strip() for k in keywords if len(k.strip()) >= 2]))

'''

content = content.replace(old_func, new_func)

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(content)

print('DONE - function replaced successfully')
