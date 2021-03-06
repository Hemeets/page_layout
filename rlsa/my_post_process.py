import numpy as np
import cv2
import matplotlib.pyplot as plt
from skimage import io, measure, filters
from PIL import Image, ImageDraw, ImageFont

'''
2019/12/13
by qidongxu
'''


COLOR_LIST = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (120, 120, 0)]  # 画box时的颜色
CLASSES_LIST = ['text', 'tableRegion', 'figureRegion', 'formulaRegion']
NAME_LIST = ['text', 'table', 'figure', 'equation']


def iteration(image: np.ndarray, value: int) -> np.ndarray:
    """
    This method iterates over the provided image by converting 255's to 0's if the number of consecutive 255's are
    less the "value" provided
    """

    rows, cols = image.shape
    for row in range(0, rows):
        try:
            # to start the conversion from the 0 pixel
            start = image[row].tolist().index(0)
        except ValueError:
            start = 0  # if '0' is not present in that row

        count = start
        for col in range(start, cols):
            if image[row, col] == 0:
                if (col-count) <= value and (col-count) > 0:
                    image[row, count:col] = 0
                count = col
    return image


def rlsa(image: np.ndarray, horizontal: bool = True, vertical: bool = True, value: int = 0) -> np.ndarray:
    """
    rlsa(RUN LENGTH SMOOTHING ALGORITHM) is to extract the block-of-text or the Region-of-interest(ROI) from the
    document binary Image provided. Must pass binary image of ndarray type.
    """

    if isinstance(image, np.ndarray):  # image must be binary of ndarray type
        # consecutive pixel position checker value to convert 255 to 0
        value = int(value) if value >= 0 else 0
        try:
            # RUN LENGTH SMOOTHING ALGORITHM working horizontally on the image
            if horizontal:
                image = iteration(image, value)

            # RUN LENGTH SMOOTHING ALGORITHM working vertically on the image
            if vertical:
                image = image.T
                image = iteration(image, value)
                image = image.T

        except (AttributeError, ValueError) as e:
            image = None
            print("ERROR: ", e, "\n")
            print('Image must be an numpy ndarray and must be in "binary". Use Opencv/PIL to convert the image to binary.\n')
            print("import cv2;\nimage=cv2.imread('path_of_the_image');\ngray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY);\n\
                (thresh, image_binary) = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)\n")
            print("method usage -- rlsa.rlsa(image_binary, True, False, 10)")
    else:
        print('Image must be an numpy ndarray and must be in binary')
        image = None
    return image


def rlsa_res_by_mask(img_rlsa, mask_class):
    '''
    img_rlsa: img after rlsa;
    mask_class: generated by skimage.measure toolbox
    type: np, 2-d
    return: img restricted by mask
    '''
    R, C = img_rlsa.shape
    rlsa_res = np.copy(img_rlsa)
    for r in range(R):
        for c in range(C):
            if (rlsa_res[r, c] == 0) and (not mask_class[r, c]):
                rlsa_res[r, c] = 255
    return rlsa_res


#  针对文本用 rlsa
def bbox_from_rlsa(img_rlsa, mask, label_num):
    '''
    mask: 3-d, channel 1-5 分别是：背景，文本，表格，图片，公式
    label_num: 与mask channel对应， 0背景，1文本，2表格，3图片，4公式
    return: bboxes: numpy格式的bbox
            labels: label_num
    '''
    mask_classes = np.argmax(mask, axis=2)
    mask_class = (mask_classes == label_num)
    img_rlsa_res = rlsa_res_by_mask(img_rlsa, mask_class)
    img_rlsa_res = 255 - img_rlsa_res  # 这里取决于二值化时，是否把背景设为白，如果是就需要翻转
    rlsa_label = measure.label(img_rlsa_res, connectivity=1)
    rlsa_props = measure.regionprops(rlsa_label)
    rlsa_boxes = [r['bbox'] for r in rlsa_props]
    rlsa_boxes = np.reshape(rlsa_boxes, (-1, 4))
    # labels = np.int32([label_num] * len(rlsa_boxes))
    return rlsa_boxes  # , labels


# 针对图片表格，直接用热图
def bbox_from_mask(mask, c):
    '''
    c: label {2-table, 3-figure}
    '''
    height, width, num_classes = mask.shape
    mask_classes = np.argmax(mask, axis=2)

    mask_class = np.int32(mask_classes == c)
    mask_label = measure.label(mask_class, connectivity=1)
    props = measure.regionprops(mask_label)

    bboxs_class = [prop['bbox'] for prop in props]
    bboxs_class = np.reshape(bboxs_class, (-1, 4))

    labels = c * np.ones(len(bboxs_class))

    confs_class = []
    for idx in range(len(bboxs_class)):
        bbox = bboxs_class[idx, :]
        conf = np.mean(mask[bbox[0]:bbox[2], bbox[1]:bbox[3], c]) / 255.0
        confs_class.append(conf)

    # Format
    bboxs_class = np.int32(bboxs_class)
    labels = np.int32(labels)
    return bboxs_class, labels, confs_class


def draw_bbox(img, bboxs, labels):
    ''' Visualization of detection results. '''
    img_pred = Image.fromarray(img)
    draw_pred = ImageDraw.Draw(img_pred)
    for i in range(bboxs.shape[0]):
        color = COLOR_LIST[labels[i] - 1]
        name = NAME_LIST[labels[i] - 1]
        bbox = bboxs[i, :]
        draw_pred.line([bbox[1], bbox[0], bbox[3], bbox[0]],
                       fill=color, width=3)
        draw_pred.line([bbox[1], bbox[2], bbox[3], bbox[2]],
                       fill=color, width=3)
        draw_pred.line([bbox[1], bbox[0], bbox[1], bbox[2]],
                       fill=color, width=3)
        draw_pred.line([bbox[3], bbox[0], bbox[3], bbox[2]],
                       fill=color, width=3)
        draw_pred.text([bbox[1], bbox[0] - 10], name, fill=color)
        # draw_pred.text([bbox[3] - 160, bbox[2] + 5], name + \
        #     ' %.3f' %confs[i], font=FONT, fill=color)
    img_return = np.array(img_pred)
    return img_return


def MergeTextBBox_col(boxes, value1=15, value2=8):  # 横向合并
    N = len(boxes)
    if N <= 1:
        return boxes
    i = 1
    out_boxes = []
    A = boxes[0]
    for i in range(1, N):
        B = boxes[i]
        if (abs(A[0] - B[0]) < value2 and abs(A[2] - B[2]) < value2 and
                min(abs(B[1] - A[3]), abs(A[1] - B[3])) < value1 + 3):
            AmergeB = [min(A[0], B[0]), min(A[1], B[1]),
                       max(A[2], B[2]), max(A[3], B[3])]
            # AmergeB = [A[0], A[1], B[2], B[3]]
            A = AmergeB
            # print("i = ", i)
            # print("merge:", AmergeB)
        else:  # 没有合并
            out_boxes.append(A)
            A = B
    out_boxes.append(A)  # 处理边界条件 i= N-1，加上最后一个
    # out_boxes = np.array(out_boxes)
    return out_boxes


def PreForRowMerge(boxes):  # 先按col大致分set，再set内排序
    '''
    boxes dtype: list
    '''
    boxes = sorted(boxes, key=lambda x: x[1])
    N = len(boxes)
    if N <= 1:
        return boxes
    tmp = []
    thresh = 50
    out_boxes = []
    tmp.append(boxes[0])
    for i in range(1, N):
        A = boxes[i-1]
        B = boxes[i]
        if abs(B[1]-A[1]) < thresh:
            tmp.append(B)
        else:
            tmp = sorted(tmp, key=lambda x: x[0])
            out_boxes += tmp
            tmp = [B]
    out_boxes += tmp
    return out_boxes


def MergeTextBBox_row(boxes, value1=15, value2=8):  # 纵向合并
    N = len(boxes)
    if N <= 1:
        return boxes
    i = 1
    out_boxes = []
    A = boxes[0]
    for i in range(1, N):
        B = boxes[i]
        if (abs(B[1] - A[1]) < value1 + 3 and abs(B[3] - A[3]) < value1 and
                min(abs(B[0] - A[2]), abs(A[0] - B[2])) < 2 * value1):
            AmergeB = [min(A[0], B[0]), min(A[1], B[1]),
                       max(A[2], B[2]), max(A[3], B[3])]
            # AmergeB = [A[0], A[1], B[2], B[3]]
            A = AmergeB
            # print("i = ", i)
            # print("merge:", AmergeB)
        else:  # 没有合并
            out_boxes.append(A)
            A = B
    out_boxes.append(A)  # 处理边界条件 i= N-1，加上最后一个
    out_boxes = np.array(out_boxes)
    return out_boxes


def process_one(img, mask, ifshow=False):
    value1 = 15
    value2 = 8
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    (thresh, image_binary) = cv2.threshold(
        gray, 150, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    rlsa_thresh_h, rlsa_thresh_v = 15, 8
    img_rlsa = rlsa(image_binary, True, False, rlsa_thresh_h)
    img_rlsa = rlsa(img_rlsa, False, True, rlsa_thresh_v)

    text_rlsa_boxes = bbox_from_rlsa(img_rlsa, mask, 1)
    text_rlsa_boxes_ = text_rlsa_boxes.tolist()
    merge_boxes = MergeTextBBox_col(text_rlsa_boxes_, value1, value2)  # 横向合并
    merge_boxes = sorted(merge_boxes, key=lambda x: x[1])
    merge_boxes = PreForRowMerge(merge_boxes)
    text_rlsa_boxes = MergeTextBBox_row(merge_boxes, value1, value2)
    text_labels = np.int32([1] * len(text_rlsa_boxes))
    # print('bboxes number of text : %d' % len(text_rlsa_boxes))
    table_boxes, table_labels, table_confs = bbox_from_mask(mask, 2)
    # print('bboxes number of table : %d' % len(table_boxes))
    figure_boxes, figure_labels, figure_confs = bbox_from_mask(mask, 3)
    # print('bboxes number of figure : %d' % len(figure_boxes))
    formula_rlsa_boxes = bbox_from_rlsa(img_rlsa, mask, 4)
    formula_labels = np.int32([4] * len(formula_rlsa_boxes))
    # print('bboxes number of formula : %d' % len(formula_rlsa_boxes))

    # 上面4类分开写是因为，不同类的处理方可能不同，先留有余地
    # print(text_rlsa_boxes.shape)
    # print(table_boxes.shape)
    # print(figure_boxes.shape)
    # print(formula_rlsa_boxes.shape)

    boxes = np.concatenate((text_rlsa_boxes, table_boxes,
                            figure_boxes, formula_rlsa_boxes), axis=0)
    labels = np.concatenate(
        (text_labels, table_labels, figure_labels, formula_labels), axis=0)
    # print(boxes.shape)
    # print(labels.shape)

    process_one_img = draw_bbox(img, boxes, labels)
    if ifshow:
        Image.fromarray(process_one_img).show()
    return process_one_img


def main():
    img_path = "E:/project/jupyter/rlsa/img/1610QB02583_page42.jpg"
    mask_path = "E:/project/jupyter/rlsa/img/1610QB02583_page42.npy"
    mask = np.load(mask_path)
    img = io.imread(img_path)  # cv2.imread 也可以
    save_path = r'E:\project\table\rlsa\1.jpg'
    one = process_one(img, mask, ifshow=True)
    # plt.imsave(save_path, one)

if __name__ == "__main__":
    main()

# 2019/12/15
# 目前速度有点慢:  1. 中间的 PreForRowMerge 中是用list处理的而不是np，其实可以全部改成 np 的
#                 2. 想一下，在mask限制 rlsa 时，能不能同时处理 4 类，而不是分开处理，如果可以速度将提升 4x

# 思考：这种先rlsa的方法，应该只针对文本比较好，因为表格和图片rlsa类内就会出现很多嵌套，效果并不好，
# 除了text外，其他的还是直接在mask外画框比较好，用1708OP01183_page86.jpg 这个图可以说明这个问题
