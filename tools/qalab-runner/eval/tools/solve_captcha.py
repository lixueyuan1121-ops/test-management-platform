# -*- coding: utf-8 -*-
# 图形验证码识别（离线 ddddocr / onnxruntime）。
# 供 Node 侧 CaptchaSolver 以「常驻子进程 + 行协议」方式调用，避免每张验证码都重载 ONNX 模型。
#
# 协议：启动后向 stderr 打印一行 "READY"（表示模型加载完成）；随后从 stdin 按行读取，
#   每行是一张验证码图片的 base64（可带 data:image/...;base64, 前缀），
#   识别后向 stdout 打印一行结果（去空白）。空行回空行；收到 "__QUIT__" 退出。
#   识别异常打印 "__ERR__ <msg>"。stdout 一行对应 stdin 一行，严格顺序。
import sys, base64

def main():
    try:
        import ddddocr
    except Exception as e:
        sys.stderr.write('IMPORT_FAIL %s\n' % e); sys.stderr.flush(); sys.exit(3)
    # beta=False 用默认 old 模型（对这类数字字母扭曲码更稳）；show_ad 关广告
    ocr = ddddocr.DdddOcr(show_ad=False)
    sys.stderr.write('READY\n'); sys.stderr.flush()
    for line in sys.stdin:
        s = line.strip()
        if not s:
            sys.stdout.write('\n'); sys.stdout.flush(); continue
        if s == '__QUIT__':
            break
        if s.startswith('data:') and ',' in s:
            s = s.split(',', 1)[1]
        try:
            img = base64.b64decode(s)
            res = (ocr.classification(img) or '').strip()
            sys.stdout.write(res + '\n'); sys.stdout.flush()
        except Exception as e:
            sys.stdout.write('__ERR__ ' + str(e).replace('\n', ' ') + '\n'); sys.stdout.flush()

if __name__ == '__main__':
    main()
