
import os
import random
import time
import xml.dom.minidom

import numpy as np
from tqdm import tqdm
from skimage import io, measure, color, filters
from PIL import Image, ImageDraw, ImageFont
from matplotlib import pyplot as plt


COLOR_LIST = [(255, 0, 0), (0, 0, 255), (0, 255, 0)]
CLASSES_LIST = ['figureRegion', 'tableRegion', 'formulaRegion']
NAME_LIST = ['figure', 'table', 'equation']
FONT = ImageFont.truetype(
    '/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf', 20)


def cut_from_masks(mask, small_object_thresh=100, expand_thresh=0.03):
    ''' Cut image regions from the mask generated by FCN '''

    height, width, num_classes = mask.shape
    mask_classes = np.argmax(mask, axis=2)

    bboxs = []
    labels = []
    confs = []

    # Figures=1, Tables=2, Equations=3.
    for c in range(1, 4):

        mask_class = np.int32(mask_classes == c)
        mask_label = measure.label(mask_class, connectivity=1)
        props = measure.regionprops(mask_label)

        bboxs_class = [prop['bbox'] for prop in props]
        bboxs_class = np.reshape(bboxs_class, (-1, 4))
        bboxs.append(bboxs_class)
        labels.append(c * np.ones(len(bboxs_class)))

        confs_class = []
        for idx in range(len(bboxs_class)):
            bbox = bboxs_class[idx, :]
            conf = np.mean(mask[bbox[0]:bbox[2], bbox[1]:bbox[3], c])
            confs_class.append(conf)
        confs.append(confs_class)

    # Concatenate
    bboxs = np.concatenate((bboxs[0], bboxs[1], bboxs[2]), axis=0)
    labels = np.concatenate((labels[0], labels[1], labels[2]), axis=0)
    confs = np.concatenate((confs[0], confs[1], confs[2]))

    # Eliminate small regions.
    area = (bboxs[:, 2] - bboxs[:, 0]) * (bboxs[:, 3] - bboxs[:, 1])
    bboxs = bboxs[area > small_object_thresh, :]
    labels = labels[area > small_object_thresh]
    confs = confs[area > small_object_thresh]

    # Expand for a small thresh
    height_rec = bboxs[:, 2] - bboxs[:, 0]
    width_rec = bboxs[:, 3] - bboxs[:, 1]
    expand = np.int64(np.minimum(width_rec, height_rec) * expand_thresh)

    bboxs[:, 0] = np.maximum(0, bboxs[:, 0] - expand)
    bboxs[:, 1] = np.maximum(0, bboxs[:, 1] - expand)
    bboxs[:, 2] = np.minimum(height, bboxs[:, 2] + expand)
    bboxs[:, 3] = np.minimum(width, bboxs[:, 3] + expand)

    # Format
    bboxs = np.int32(bboxs)
    labels = np.int32(labels)

    return bboxs, labels, confs


def modify_boundary(img_bw):
    ''' Remove white edges '''

    img = 1 - np.float64(img_bw)

    sum_ver = np.concatenate(
        (np.zeros(1), np.sum(img, 0), np.zeros(1)), axis=0)
    sum_hor = np.concatenate(
        (np.zeros(1), np.sum(img, 1), np.zeros(1)), axis=0)

    diff_ver = np.diff(sum_ver)
    diff_hor = np.diff(sum_hor)

    # print(diff_ver)
    x_0 = np.where(diff_ver > 0)[0][0]
    x_1 = np.where(diff_ver < 0)[0][-1]
    y_0 = np.where(diff_hor > 0)[0][0]
    y_1 = np.where(diff_hor < 0)[0][-1]

    return [y_0, x_0, y_1, x_1]


def merge_bbox(bbox1, bbox2):
    ''' Merge 2 bounding boxes into a large one. '''

    new_bbox = np.zeros((1, 4))
    new_bbox[0:2] = np.minimum(bbox1[0:2], bbox2[0:2])
    new_bbox[2:4] = np.maximum(bbox1[2:4], bbox2[2:4])
    return new_bbox


def rlsa(img_bw, hor=True):
    ''' RLSA for modify boundary '''

    height, width = img_bw.shape

    if hor == True:
        padding = np.int32(width / 2.0)
    else:
        padding = np.int32(height / 40.0)

    img_pad = np.concatenate((np.zeros((height, 1)), np.ones((height, padding)),
                              img_bw, np.ones((height, padding)), np.zeros((height, 1))), axis=1)
    img_array = np.reshape(img_pad, (1, -1))

    diff_array = np.diff(img_array)
    start = np.nonzero(diff_array == 1)[1]
    stop = np.nonzero(diff_array == -1)[1]
    length = stop - start

    threshold = padding
    idx = length < threshold

    diff_array[0, start[idx]] = 0
    diff_array[0, stop[idx]] = 0

    img_rlsa = np.cumsum(np.append(0, diff_array))
    img_rlsa = np.reshape(img_rlsa, (height, -1))
    img_rlsa = img_rlsa[:, threshold+1:-threshold-1]

    return img_rlsa


def figure_process(img, mask, bboxs, lables, confs):
    '''figure cut and white boundary remove '''

    bboxs_new = np.reshape([], (-1, 4))
    labels_new = np.reshape([], (-1, ))
    confs_new = np.reshape([], (-1, ))

    for i in range(len(bboxs)):

        x1 = bboxs[i, 0]
        y1 = bboxs[i, 1]
        x2 = bboxs[i, 2]
        y2 = bboxs[i, 3]

        image = img[x1:x2, y1:y2]
        image_bw = image > 0.9  # Binarization

        # Starting and ending indexes (by vertical projection)
        projection = np.sum(1 - image_bw, axis=0)
        projection = np.concatenate((np.zeros(1), projection, np.zeros(1)))
        projection = np.array(projection > 0, dtype=np.int32)
        idx_start = np.where(np.diff(projection) == 1)[0]
        idx_end = np.where(np.diff(projection) == -1)[0]

        # Probably white image (no start and end index)
        if len(idx_start) < 1:
            continue
        else:
            modify_idx = modify_boundary(image_bw)

        # Remove cut likely caused by noise (too small width)
        cut_width = idx_start[1:] - idx_end[:-1]
        delete_idx = np.where(cut_width <= 1)[0]
        if len(delete_idx) > 0:
            idx_start = np.delete(idx_start, delete_idx + 1)
            idx_end = np.delete(idx_end, delete_idx)

        # Remove probabely a bad cut (Width deviation too large)
        cut_length = idx_end - idx_start
        cut_deviation = np.std(cut_length) / np.median(cut_length)
        if cut_deviation > 0.1:
            idx_start = [idx_start[0]]
            idx_end = [idx_end[-1]]

        # For each cut, update bboxs, labels and confs
        for start, end in zip(idx_start, idx_end):
            bbox_new = np.reshape([x1 + modify_idx[0], y1 + start,
                                   x1 + modify_idx[2] - 1, y1 + end - 1], (1, 4))
            conf_new = np.mean(mask[bbox_new[0, 0]:bbox_new[0, 2] + 1,
                                    bbox_new[0, 1]:bbox_new[0, 3] + 1, 1])
            bboxs_new = np.concatenate((bboxs_new, bbox_new))
            labels_new = np.append(labels_new, 1)
            confs_new = np.append(confs_new, conf_new)

    bboxs_new = np.int32(bboxs_new)
    labels_new = np.int32(labels_new)

    height = bboxs_new[:, 2] - bboxs_new[:, 0]
    width = bboxs_new[:, 3] - bboxs_new[:, 1]
    area = width * height
    # remain_list = np.logical_and(width > 10, height > 0, area > 0)
    remain_list = np.logical_and(width > 0, height > 0, area > 0)

    bboxs_new = bboxs_new[remain_list, :]
    labels_new = labels_new[remain_list]
    confs_new = confs_new[remain_list]

    return bboxs_new, labels_new, confs_new


def table_process(img, mask, bboxs, labels, confs):
    ''' boundary remove '''

    bboxs_new = np.reshape([], (-1, 4))
    labels_new = np.reshape([], (-1, ))
    confs_new = np.reshape([], (-1, ))

    for i in range(len(bboxs)):

        x1 = bboxs[i, 0]
        y1 = bboxs[i, 1]
        x2 = bboxs[i, 2]
        y2 = bboxs[i, 3]

        image = img[x1:x2, y1:y2]
        image = image > 0.9

        # Remove white
        if np.sum(1 - image) == 0:
            continue
        else:
            modify_idx = modify_boundary(image)

        bbox_new = [[x1 + modify_idx[0], y1 + modify_idx[1],
                     x1 + modify_idx[2], y1 + modify_idx[3]]]
        conf_new = np.mean(mask[bbox_new[0][0]:bbox_new[0][2],
                                bbox_new[0][1]:bbox_new[0][3], 2])

        bboxs_new = np.concatenate((bboxs_new, bbox_new), axis=0)
        labels_new = np.append(labels_new, 2)
        confs_new = np.append(confs_new, conf_new)

    bboxs_new = np.int32(bboxs_new)
    labels_new = np.int32(labels_new)

    width = bboxs_new[:, 2] - bboxs_new[:, 0]
    height = bboxs_new[:, 3] - bboxs_new[:, 1]
    area = width * height
    # remain_list = np.logical_and(width > 30, height > 30, area > 1000)
    remain_list = np.logical_and(width > 0, height > 0, area > 0)

    bboxs_new = bboxs_new[remain_list, :]
    labels_new = labels_new[remain_list]
    confs_new = confs_new[remain_list]

    return bboxs_new, labels_new, confs_new


def equation_process(img, mask, bboxs, lables, confs):
    '''equation cut by rlsa'''

    bboxs_new = np.reshape([], (-1, 4))
    labels_new = np.reshape([], (-1, ))
    confs_new = np.reshape([], (-1, ))

    for i in range(len(bboxs)):

        x1 = bboxs[i, 0]
        y1 = bboxs[i, 1]
        x2 = bboxs[i, 2]
        y2 = bboxs[i, 3]

        image = img[x1:x2, y1:y2]
        image = image > 0.9
        image = rlsa(image)
        #image = rlsa_for_one(image.T,hor = False).T

        if np.min(image.shape) <= 1:
            continue

        # Find new bboxs in rlsa result
        image_label = measure.label(1 - image)
        props = measure.regionprops(image_label)
        bboxs_rlsa = [prop['bbox'] for prop in props]
        bboxs_rlsa = np.array(bboxs_rlsa, np.int32)

        if len(bboxs_rlsa) == 0:
            continue

        bboxs_rlsa[:, 0] += x1
        bboxs_rlsa[:, 1] += y1
        bboxs_rlsa[:, 2] += x1
        bboxs_rlsa[:, 3] += y1

        # Updata bboxs, labels and confs
        for bbox_rlsa in bboxs_rlsa:
            conf_rlsa = np.mean(mask[bbox_rlsa[0]:bbox_rlsa[2],
                                     bbox_rlsa[1]:bbox_rlsa[3], 3])
            bboxs_new = np.concatenate((bboxs_new, [bbox_rlsa]), axis=0)
            labels_new = np.append(labels_new, 3)
            confs_new = np.append(confs_new, conf_rlsa)

    # print(bboxs_new)
    bboxs_new = np.int32(bboxs_new)
    labels_new = np.int32(labels_new)

    height = bboxs_new[:, 2] - bboxs_new[:, 0]
    width = bboxs_new[:, 3] - bboxs_new[:, 1]
    area = width * height
    # remain_list = np.logical_and(width > 10, height > 0, area > 0)
    remain_list = np.logical_and(width > 0, height > 0, area > 0)

    bboxs_new = bboxs_new[remain_list, :]
    labels_new = labels_new[remain_list]
    confs_new = confs_new[remain_list]

    return bboxs_new, labels_new, confs_new


def bbox_overlap(bboxs, labels, confs, overlap_thresh=0.8, small_thresh=30):

    areas = (bboxs[:, 2] - bboxs[:, 0]) * (bboxs[:, 3] - bboxs[:, 1])

    # Remove bboxs with area less than a small threshold
    bboxs = bboxs[areas > small_thresh, :]
    labels = labels[areas > small_thresh]
    confs = confs[areas > small_thresh]
    areas = areas[areas > small_thresh]

    # if there is less than one bboxs then return.
    if len(bboxs) <= 1:
        return bboxs, labels, confs

    areas = (bboxs[:, 2] - bboxs[:, 0]) * (bboxs[:, 3] - bboxs[:, 1])
    overlap = np.zeros((bboxs.shape[0], bboxs.shape[0]))

    # Compute the overlap ratio by the given bounding boxes.
    for i, (bbox1, label1) in enumerate(zip(bboxs, labels)):
        for j, (bbox2, label2) in enumerate(zip(bboxs, labels)):
            # Overlaps within the same class label are set to 0.
            if label1 != label2 or i == j:
                continue
            overlap_width = np.min((bbox1[2], bbox2[2])) - \
                np.max((bbox1[0], bbox2[0]))
            overlap_height = np.min((bbox1[3], bbox2[3])) - \
                np.max((bbox1[1], bbox2[1]))
            overlap_area = np.max((overlap_height, 0)) * \
                np.max((overlap_width, 0))
            overlap[i, j] = overlap_area / np.float32(areas[i])

    while True:
        # Remove the duplicate bounding boxes by overlap ratio threshold.
        idx = np.where(overlap > overlap_thresh)
        if len(idx[0]) == 0:
            break

        # Update bounding box overlap matrix.
        delete_idx = idx[0][0]
        overlap = np.delete(overlap, delete_idx, axis=0)
        overlap = np.delete(overlap, delete_idx, axis=1)

        # Update bboxs, labels and confs.
        bboxs = np.delete(bboxs, delete_idx, axis=0)
        labels = np.delete(labels, delete_idx)
        confs = np.delete(confs, delete_idx)

    return bboxs, labels, confs


def bbox_overlap_back(bboxs, labels, confs, overlap_thresh=0.6, small_thresh=30):
    ''' Compute overlap ratio matrix on given bboxes '''

    areas = (bboxs[:, 2] - bboxs[:, 0]) * (bboxs[:, 3] - bboxs[:, 1])

    # Remove bboxs with area less than a small threshold
    bboxs = bboxs[areas > small_thresh, :]
    labels = labels[areas > small_thresh]
    confs = confs[areas > small_thresh]
    areas = areas[areas > small_thresh]

    if len(bboxs) <= 1:
        return bboxs, labels, confs

    overlap = np.zeros((bboxs.shape[0], bboxs.shape[0]))

    for i in range(bboxs.shape[0]):
        bbox1 = bboxs[i, :]
        for j in range(bboxs.shape[0]):
            if i != j:
                bbox2 = bboxs[j, :]
                overlap_width = np.min((bbox1[2], bbox2[2])) - \
                    np.max((bbox1[0], bbox2[0]))
                overlap_height = np.min((bbox1[3], bbox2[3])) - \
                    np.max((bbox1[1], bbox2[1]))
                overlap_area = np.max((overlap_height, 0)) * \
                    np.max((overlap_width, 0))
                overlap[i, j] = overlap_area / np.float32(areas[i])

    bboxs = np.int32(bboxs[np.all(overlap < overlap_thresh, axis=1)])
    labels = np.int32(labels[np.all(overlap < overlap_thresh, axis=1)])
    confs = confs[np.all(overlap < overlap_thresh, axis=1)]

    return bboxs, labels, confs


def write_xml(root, doc, name, bboxs, labels, confs):
    ''' write the outcomes to xml file'''

    document = doc.createElement('document')
    document.setAttribute('filename', name + '.bmp')
    root.appendChild(document)

    for i in range(len(bboxs)):

        bbox = bboxs[i]
        points = str(bbox[1]) + ',' + str(bbox[0]) + ' ' + \
            str(bbox[3]) + ',' + str(bbox[0]) + ' ' + \
            str(bbox[1]) + ',' + str(bbox[2])+' ' + \
            str(bbox[3]) + ',' + str(bbox[2])

        region = doc.createElement(CLASSES_LIST[labels[i] - 1])
        region.setAttribute('prob', str(confs[i]))
        document.appendChild(region)
        bbox = doc.createElement('Coords')
        bbox.setAttribute('points', points)
        region.appendChild(bbox)


def draw_bbox(img, bboxs, labels, confs, gt=None):
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
        draw_pred.text([bbox[3] - 160, bbox[2] + 5], name +
                       ' %.3f' % confs[i], font=FONT, fill=color)

    if gt:

        img_gt = Image.fromarray(img)
        draw_gt = ImageDraw.Draw(img_gt)

        for gt_line in gt:

            bbox = np.int32(gt_line.split('\t')[0].split(','))
            name = gt_line.split('\t')[1].split('\n')[0]

            color = None
            if name == 'figure':
                color = COLOR_LIST[0]
            elif name == 'table':
                color = COLOR_LIST[1]
            elif name == 'formula':
                color = COLOR_LIST[2]

            if color:
                draw_gt.line([bbox[0], bbox[2], bbox[1], bbox[2]],
                             fill=color, width=3)
                draw_gt.line([bbox[0], bbox[3], bbox[1], bbox[3]],
                             fill=color, width=3)
                draw_gt.line([bbox[0], bbox[2], bbox[0], bbox[3]],
                             fill=color, width=3)
                draw_gt.line([bbox[1], bbox[2], bbox[1], bbox[3]],
                             fill=color, width=3)
                draw_gt.text([bbox[1] - 75, bbox[3] + 5],
                             name, font=FONT, fill=color)

        img_return = np.concatenate(
            (np.array(img_pred), np.array(img_gt)), axis=1)

    else:
        img_return = np.array(img_pred)

    return img_return


def process_one(img, mask):
    ''' process one image '''

    bboxs, labels, confs = cut_from_masks(mask)

    figure_idx = np.where(labels == 1)[0]
    bboxs_figure = bboxs[figure_idx]
    labels_figure = labels[figure_idx]
    confs_figure = confs[figure_idx]
    bboxs_figure, labels_figure, confs_figure = \
        figure_process(img, mask, bboxs_figure, labels_figure, confs_figure)

    table_idx = np.where(labels == 2)[0]
    bboxs_table = bboxs[table_idx]
    labels_table = labels[table_idx]
    confs_table = confs[table_idx]
    bboxs_table, labels_table, confs_table = \
        table_process(img, mask, bboxs_table, labels_table, confs_table)

    equation_idx = np.where(labels == 3)[0]
    bboxs_equation = bboxs[equation_idx]
    labels_equation = labels[equation_idx]
    confs_equation = confs[equation_idx]
    bboxs_equation, labels_equation, confs_equation = \
        equation_process(img, mask, bboxs_equation,
                         labels_equation, confs_equation)

    bboxs = np.concatenate((bboxs_figure, bboxs_table, bboxs_equation), axis=0)
    labels = np.concatenate((labels_figure, labels_table, labels_equation))
    confs = np.concatenate((confs_figure, confs_table, confs_equation))

    bboxs, labels, confs = bbox_overlap(bboxs, labels, confs)
    return bboxs, labels, confs


def test_one(img_path, mask_path, vis=False, gt_path=None):
    ''' Test on one given pair of image and mask. '''

    img_raw = io.imread(img_path)
    img = color.rgb2gray(img_raw)
    mask = np.load(mask_path)

    bboxs, labels, confs = process_one(img, mask)

    if vis:
        gt = open(gt_path, 'r').readlines()
        img_show = draw_bbox(img_raw, bboxs, labels, confs, gt)
        Image.fromarray(img_show).show()

    return bboxs, labels, confs


def test_all(img_dir, mask_dir, output_file='submission.xml', output_dir=None, gt_dir=None):
    ''' Test on a set of images and save the predicion xml file '''

    doc = xml.dom.minidom.Document()
    root = doc.createElement('')
    doc.appendChild(root)

    masks = os.listdir(mask_dir)
    for mask in tqdm(masks):

        if not mask.endswith('.png'):
            continue

        name = mask.split('_pred.png')[0]
        # print name

        img_path = img_dir + name + '.jpg'
        img_raw = io.imread(img_path)
        img = color.rgb2gray(img_raw)

        mask_path = mask_dir + name + '_prob.npy'
        mask = np.load(mask_path)

        bboxs, labels, confs = process_one(img, mask)
        write_xml(root, doc, name, bboxs, labels, confs)

        if output_dir:
            gt = open(gt_dir + name + '.txt', 'r').readlines()
            img_save = draw_bbox(img_raw, bboxs, labels, confs, gt)
            plt.imsave(output_dir + name + '_bbox.jpg', img_save)

    with open(output_file, 'w') as xml_file:
        doc.writexml(xml_file, newl='\n', addindent='\t', encoding='UTF-8')


if __name__ == '__main__':

    img_dir = '../pod_test/images/'
    gt_dir = '../../pod/Test/Annotations/'
    mask_dir = '../pod_test/predictions+/'
    output_dir = '../pod_test/results/results+/'
    output_file = '../pod_test/submission.xml'

    img_id = random.randint(1600, 2416)
    img_id = 2366

    # Test on POD test set
    test_all(img_dir, mask_dir, output_file=output_file,
             output_dir=None, gt_dir=gt_dir)

    # ### Test on random image
    # img_path = img_dir + 'POD_%d.jpg' %img_id
    # mask_path = mask_dir + 'POD_%d_prob.npy' %img_id
    # gt_path = gt_dir + 'POD_%d.txt' %img_id
    # bboxs, labels, confs = test_one(img_path, mask_path, vis=True, gt_path=gt_path)
