#! /usr/bin/python

# Multistage table builder
# (c) Peter Kankowski, 2008

##############################################################################
# This script was submitted to the PCRE project by Peter Kankowski as part of
# the upgrading of Unicode property support. The new code speeds up property
# matching many times. The script is for the use of PCRE maintainers, to
# generate the pcre_ucd.c file that contains a digested form of the Unicode
# data tables.
#
# The script has now been upgraded to Python 3 for PCRE2, and should be run in 
# the maint subdirectory, using the command
#
# [python3] ./MultiStage2.py >../src/pcre2_ucd.c
#
# It requires four Unicode data tables, DerivedGeneralCategory.txt,
# GraphemeBreakProperty.txt, Scripts.txt, and CaseFolding.txt, to be in the 
# Unicode.tables subdirectory. The first of these is found in the "extracted" 
# subdirectory of the Unicode database (UCD) on the Unicode web site; the 
# second is in the "auxiliary" subdirectory; the other two are directly in the 
# UCD directory.
#
# Minor modifications made to this script:
#  Added #! line at start
#  Removed tabs
#  Made it work with Python 2.4 by rewriting two statements that needed 2.5
#  Consequent code tidy
#  Adjusted data file names to take from the Unicode.tables directory
#  Adjusted global table names by prefixing _pcre_.
#  Commented out stuff relating to the casefolding table, which isn't used;
#    removed completely in 2012.
#  Corrected size calculation
#  Add #ifndef SUPPORT_UCP to use dummy tables when no UCP support is needed.
#  Update for PCRE2: name changes, and SUPPORT_UCP is abolished.
#
# Major modifications made to this script:
#  Added code to add a grapheme break property field to records.
#
#  Added code to search for sets of more than two characters that must match
#  each other caselessly. A new table is output containing these sets, and
#  offsets into the table are added to the main output records. This new
#  code scans CaseFolding.txt instead of UnicodeData.txt.
#
#  Update for Python3:
#    . Processed with 2to3, but that didn't fix everything
#    . Changed string.strip to str.strip
#    . Added encoding='utf-8' to the open() call
#    . Inserted 'int' before blocksize/ELEMS_PER_LINE because an int is
#        required and the result of the division is a float
#
# The main tables generated by this script are used by macros defined in
# pcre2_internal.h. They look up Unicode character properties using short 
# sequences of code that contains no branches, which makes for greater speed.
#
# Conceptually, there is a table of records (of type ucd_record), containing a
# script number, character type, grapheme break type, offset to caseless
# matching set, and offset to the character's other case for every character.
# However, a real table covering all Unicode characters would be far too big.
# It can be efficiently compressed by observing that many characters have the
# same record, and many blocks of characters (taking 128 characters in a block)
# have the same set of records as other blocks. This leads to a 2-stage lookup
# process.
#
# This script constructs four tables. The ucd_caseless_sets table contains
# lists of characters that all match each other caselessly. Each list is
# in order, and is terminated by NOTACHAR (0xffffffff), which is larger than
# any valid character. The first list is empty; this is used for characters
# that are not part of any list.
#
# The ucd_records table contains one instance of every unique record that is
# required. The ucd_stage1 table is indexed by a character's block number, and
# yields what is in effect a "virtual" block number. The ucd_stage2 table is a
# table of "virtual" blocks; each block is indexed by the offset of a character
# within its own block, and the result is the offset of the required record.
#
# Example: lowercase "a" (U+0061) is in block 0
#          lookup 0 in stage1 table yields 0
#          lookup 97 in the first table in stage2 yields 16
#          record 17 is { 33, 5, 11, 0, -32 } 
#            33 = ucp_Latin   => Latin script
#             5 = ucp_Ll      => Lower case letter
#            11 = ucp_gbOther => Grapheme break property "Other"
#             0               => not part of a caseless set
#           -32               => Other case is U+0041
#         
# Almost all lowercase latin characters resolve to the same record. One or two
# are different because they are part of a multi-character caseless set (for
# example, k, K and the Kelvin symbol are such a set).
#
# Example: hiragana letter A (U+3042) is in block 96 (0x60)
#          lookup 96 in stage1 table yields 88
#          lookup 66 in the 88th table in stage2 yields 467
#          record 470 is { 26, 7, 11, 0, 0 } 
#            26 = ucp_Hiragana => Hiragana script
#             7 = ucp_Lo       => Other letter
#            11 = ucp_gbOther  => Grapheme break property "Other"
#             0                => not part of a caseless set
#             0                => No other case 
#
# In these examples, no other blocks resolve to the same "virtual" block, as it
# happens, but plenty of other blocks do share "virtual" blocks.
#
# There is a fourth table, maintained by hand, which translates from the 
# individual character types such as ucp_Cc to the general types like ucp_C.
#
#  Philip Hazel, 03 July 2008
#
# 01-March-2010:     Updated list of scripts for Unicode 5.2.0
# 30-April-2011:     Updated list of scripts for Unicode 6.0.0
#     July-2012:     Updated list of scripts for Unicode 6.1.0
# 20-August-2012:    Added scan of GraphemeBreakProperty.txt and added a new 
#                      field in the record to hold the value. Luckily, the 
#                      structure had a hole in it, so the resulting table is
#                      not much bigger than before.
# 18-September-2012: Added code for multiple caseless sets. This uses the
#                      final hole in the structure.
# 30-September-2012: Added RegionalIndicator break property from Unicode 6.2.0
# 13-May-2014:       Updated for PCRE2
# 03-June-2014:      Updated for Python 3
# 20-June-2014:      Updated for Unicode 7.0.0
# 12-August-2014:    Updated to put Unicode version into the file
# 19-June-2015:      Updated for Unicode 8.0.0
##############################################################################


import re
import string
import sys

MAX_UNICODE = 0x110000
NOTACHAR = 0xffffffff


# Parse a line of Scripts.txt, GraphemeBreakProperty.txt or DerivedGeneralCategory.txt
def make_get_names(enum):
        return lambda chardata: enum.index(chardata[1])

# Parse a line of CaseFolding.txt
def get_other_case(chardata):
        if chardata[1] == 'C' or chardata[1] == 'S':
          return int(chardata[2], 16) - int(chardata[0], 16)
        return 0


# Read the whole table in memory, setting/checking the Unicode version
def read_table(file_name, get_value, default_value):
        global unicode_version
         
        f = re.match(r'^[^/]+/([^.]+)\.txt$', file_name)
        file_base = f.group(1)
        version_pat = r"^# " + re.escape(file_base) + r"-(\d+\.\d+\.\d+)\.txt$"
        file = open(file_name, 'r', encoding='utf-8')
        f = re.match(version_pat, file.readline())
        version = f.group(1)
        if unicode_version == "":
                unicode_version = version
        elif unicode_version != version:
                print("WARNING: Unicode version differs in %s", file_name, file=sys.stderr)
 
        table = [default_value] * MAX_UNICODE
        for line in file:
                line = re.sub(r'#.*', '', line)
                chardata = list(map(str.strip, line.split(';')))
                if len(chardata) <= 1:
                        continue
                value = get_value(chardata)
                m = re.match(r'([0-9a-fA-F]+)(\.\.([0-9a-fA-F]+))?$', chardata[0])
                char = int(m.group(1), 16)
                if m.group(3) is None:
                        last = char
                else:
                        last = int(m.group(3), 16)            
                for i in range(char, last + 1):
                        # It is important not to overwrite a previously set
                        # value because in the CaseFolding file there are lines
                        # to be ignored (returning the default value of 0) 
                        # which often come after a line which has already set 
                        # data.   
                        if table[i] == default_value: 
                          table[i] = value
        file.close()
        return table

# Get the smallest possible C language type for the values
def get_type_size(table):
        type_size = [("uint8_t", 1), ("uint16_t", 2), ("uint32_t", 4),
                                 ("signed char", 1), ("pcre_int16", 2), ("pcre_int32", 4)]
        limits = [(0, 255), (0, 65535), (0, 4294967295),
                          (-128, 127), (-32768, 32767), (-2147483648, 2147483647)]
        minval = min(table)
        maxval = max(table)
        for num, (minlimit, maxlimit) in enumerate(limits):
                if minlimit <= minval and maxval <= maxlimit:
                        return type_size[num]
        else:
                raise OverflowError("Too large to fit into C types")

def get_tables_size(*tables):
        total_size = 0
        for table in tables:
                type, size = get_type_size(table)
                total_size += size * len(table)
        return total_size

# Compress the table into the two stages
def compress_table(table, block_size):
        blocks = {} # Dictionary for finding identical blocks
        stage1 = [] # Stage 1 table contains block numbers (indices into stage 2 table)
        stage2 = [] # Stage 2 table contains the blocks with property values
        table = tuple(table)
        for i in range(0, len(table), block_size):
                block = table[i:i+block_size]
                start = blocks.get(block)
                if start is None:
                        # Allocate a new block
                        start = len(stage2) / block_size
                        stage2 += block
                        blocks[block] = start
                stage1.append(start)
        
        return stage1, stage2

# Print a table
def print_table(table, table_name, block_size = None):
        type, size = get_type_size(table)
        ELEMS_PER_LINE = 16
        
        s = "const %s %s[] = { /* %d bytes" % (type, table_name, size * len(table))
        if block_size:
                s += ", block = %d" % block_size
        print(s + " */")
        table = tuple(table)
        if block_size is None:
                fmt = "%3d," * ELEMS_PER_LINE + " /* U+%04X */"
                mult = MAX_UNICODE / len(table)
                for i in range(0, len(table), ELEMS_PER_LINE):
                        print(fmt % (table[i:i+ELEMS_PER_LINE] + 
                          (int(i * mult),)))
        else:
                if block_size > ELEMS_PER_LINE:
                        el = ELEMS_PER_LINE
                else:
                        el = block_size
                fmt = "%3d," * el + "\n"
                if block_size > ELEMS_PER_LINE:
                        fmt = fmt * int(block_size / ELEMS_PER_LINE)
                for i in range(0, len(table), block_size):
                        print(("/* block %d */\n" + fmt) % ((i / block_size,) + table[i:i+block_size]))
        print("};\n")

# Extract the unique combinations of properties into records
def combine_tables(*tables):
        records = {}
        index = []
        for t in zip(*tables):
                i = records.get(t)
                if i is None:
                        i = records[t] = len(records)
                index.append(i)
        return index, records

def get_record_size_struct(records):
        size = 0
        structure = '/* When recompiling tables with a new Unicode version, please check the\n' + \
        'types in this structure definition from pcre2_internal.h (the actual\n' + \
        'field names will be different):\n\ntypedef struct {\n'
        for i in range(len(records[0])):
                record_slice = [record[i] for record in records]
                slice_type, slice_size = get_type_size(record_slice)
                # add padding: round up to the nearest power of slice_size
                size = (size + slice_size - 1) & -slice_size
                size += slice_size
                structure += '%s property_%d;\n' % (slice_type, i)
        
        # round up to the first item of the next structure in array
        record_slice = [record[0] for record in records]
        slice_type, slice_size = get_type_size(record_slice)
        size = (size + slice_size - 1) & -slice_size
        
        structure += '} ucd_record;\n*/\n\n'
        return size, structure
        
def test_record_size():
        tests = [ \
          ( [(3,), (6,), (6,), (1,)], 1 ), \
          ( [(300,), (600,), (600,), (100,)], 2 ), \
          ( [(25, 3), (6, 6), (34, 6), (68, 1)], 2 ), \
          ( [(300, 3), (6, 6), (340, 6), (690, 1)], 4 ), \
          ( [(3, 300), (6, 6), (6, 340), (1, 690)], 4 ), \
          ( [(300, 300), (6, 6), (6, 340), (1, 690)], 4 ), \
          ( [(3, 100000), (6, 6), (6, 123456), (1, 690)], 8 ), \
          ( [(100000, 300), (6, 6), (123456, 6), (1, 690)], 8 ), \
        ]
        for test in tests:
            size, struct = get_record_size_struct(test[0])
            assert(size == test[1])
            #print struct

def print_records(records, record_size):
        print('const ucd_record PRIV(ucd_records)[] = { ' + \
              '/* %d bytes, record size %d */' % (len(records) * record_size, record_size))

        records = list(zip(list(records.keys()), list(records.values())))
        records.sort(key = lambda x: x[1])
        for i, record in enumerate(records):
                print(('  {' + '%6d, ' * len(record[0]) + '}, /* %3d */') % (record[0] + (i,)))
        print('};\n')

script_names = ['Arabic', 'Armenian', 'Bengali', 'Bopomofo', 'Braille', 'Buginese', 'Buhid', 'Canadian_Aboriginal', \
 'Cherokee', 'Common', 'Coptic', 'Cypriot', 'Cyrillic', 'Deseret', 'Devanagari', 'Ethiopic', 'Georgian', \
 'Glagolitic', 'Gothic', 'Greek', 'Gujarati', 'Gurmukhi', 'Han', 'Hangul', 'Hanunoo', 'Hebrew', 'Hiragana', \
 'Inherited', 'Kannada', 'Katakana', 'Kharoshthi', 'Khmer', 'Lao', 'Latin', 'Limbu', 'Linear_B', 'Malayalam', \
 'Mongolian', 'Myanmar', 'New_Tai_Lue', 'Ogham', 'Old_Italic', 'Old_Persian', 'Oriya', 'Osmanya', 'Runic', \
 'Shavian', 'Sinhala', 'Syloti_Nagri', 'Syriac', 'Tagalog', 'Tagbanwa', 'Tai_Le', 'Tamil', 'Telugu', 'Thaana', \
 'Thai', 'Tibetan', 'Tifinagh', 'Ugaritic', 'Yi', \
# New for Unicode 5.0
 'Balinese', 'Cuneiform', 'Nko', 'Phags_Pa', 'Phoenician', \
# New for Unicode 5.1
 'Carian', 'Cham', 'Kayah_Li', 'Lepcha', 'Lycian', 'Lydian', 'Ol_Chiki', 'Rejang', 'Saurashtra', 'Sundanese', 'Vai', \
# New for Unicode 5.2
 'Avestan', 'Bamum', 'Egyptian_Hieroglyphs', 'Imperial_Aramaic', \
 'Inscriptional_Pahlavi', 'Inscriptional_Parthian', \
 'Javanese', 'Kaithi', 'Lisu', 'Meetei_Mayek', \
 'Old_South_Arabian', 'Old_Turkic', 'Samaritan', 'Tai_Tham', 'Tai_Viet', \
# New for Unicode 6.0.0
 'Batak', 'Brahmi', 'Mandaic', \
# New for Unicode 6.1.0
 'Chakma', 'Meroitic_Cursive', 'Meroitic_Hieroglyphs', 'Miao', 'Sharada', 'Sora_Sompeng', 'Takri',
# New for Unicode 7.0.0
 'Bassa_Vah', 'Caucasian_Albanian', 'Duployan', 'Elbasan', 'Grantha', 'Khojki', 'Khudawadi',
 'Linear_A', 'Mahajani', 'Manichaean', 'Mende_Kikakui', 'Modi', 'Mro', 'Nabataean',
 'Old_North_Arabian', 'Old_Permic', 'Pahawh_Hmong', 'Palmyrene', 'Psalter_Pahlavi',
 'Pau_Cin_Hau', 'Siddham', 'Tirhuta', 'Warang_Citi',
# New for Unicode 8.0.0
 'Ahom', 'Anatolian_Hieroglyphs', 'Hatran', 'Multani', 'Old_Hungarian',
 'SignWriting'
 ]
 
category_names = ['Cc', 'Cf', 'Cn', 'Co', 'Cs', 'Ll', 'Lm', 'Lo', 'Lt', 'Lu',
  'Mc', 'Me', 'Mn', 'Nd', 'Nl', 'No', 'Pc', 'Pd', 'Pe', 'Pf', 'Pi', 'Po', 'Ps',
  'Sc', 'Sk', 'Sm', 'So', 'Zl', 'Zp', 'Zs' ]

break_property_names = ['CR', 'LF', 'Control', 'Extend', 'Prepend',
  'SpacingMark', 'L', 'V', 'T', 'LV', 'LVT', 'Regional_Indicator', 'Other' ]

test_record_size()
unicode_version = ""

script = read_table('Unicode.tables/Scripts.txt', make_get_names(script_names), script_names.index('Common'))
category = read_table('Unicode.tables/DerivedGeneralCategory.txt', make_get_names(category_names), category_names.index('Cn'))
break_props = read_table('Unicode.tables/GraphemeBreakProperty.txt', make_get_names(break_property_names), break_property_names.index('Other'))
other_case = read_table('Unicode.tables/CaseFolding.txt', get_other_case, 0)


# This block of code was added by PH in September 2012. I am not a Python 
# programmer, so the style is probably dreadful, but it does the job. It scans 
# the other_case table to find sets of more than two characters that must all 
# match each other caselessly. Later in this script a table of these sets is 
# written out. However, we have to do this work here in order to compute the 
# offsets in the table that are inserted into the main table.

# The CaseFolding.txt file lists pairs, but the common logic for reading data
# sets only one value, so first we go through the table and set "return" 
# offsets for those that are not already set.

for c in range(0x10ffff):
  if other_case[c] != 0 and other_case[c + other_case[c]] == 0:
    other_case[c + other_case[c]] = -other_case[c] 

# Now scan again and create equivalence sets.

sets = []

for c in range(0x10ffff):
  o = c + other_case[c]

  # Trigger when this character's other case does not point back here. We
  # now have three characters that are case-equivalent. 
 
  if other_case[o] != -other_case[c]:
    t = o + other_case[o]
    
    # Scan the existing sets to see if any of the three characters are already 
    # part of a set. If so, unite the existing set with the new set.
 
    appended = 0 
    for s in sets:
      found = 0 
      for x in s:
        if x == c or x == o or x == t:
          found = 1
    
      # Add new characters to an existing set
       
      if found:
        found = 0 
        for y in [c, o, t]:
          for x in s:
            if x == y:
              found = 1
          if not found:
            s.append(y)
        appended = 1
        
    # If we have not added to an existing set, create a new one.

    if not appended:     
      sets.append([c, o, t])

# End of loop looking for caseless sets.

# Now scan the sets and set appropriate offsets for the characters.

caseless_offsets = [0] * MAX_UNICODE

offset = 1;
for s in sets:
  for x in s:   
    caseless_offsets[x] = offset
  offset += len(s) + 1

# End of block of code for creating offsets for caseless matching sets.


# Combine the tables

table, records = combine_tables(script, category, break_props, 
  caseless_offsets, other_case)

record_size, record_struct = get_record_size_struct(list(records.keys()))

# Find the optimum block size for the two-stage table
min_size = sys.maxsize
for block_size in [2 ** i for i in range(5,10)]:
        size = len(records) * record_size
        stage1, stage2 = compress_table(table, block_size)
        size += get_tables_size(stage1, stage2)
        #print "/* block size %5d  => %5d bytes */" % (block_size, size)
        if size < min_size:
                min_size = size
                min_stage1, min_stage2 = stage1, stage2
                min_block_size = block_size

print("/* This module is generated by the maint/MultiStage2.py script.")
print("Do not modify it by hand. Instead modify the script and run it")
print("to regenerate this code.")
print()
print("As well as being part of the PCRE2 library, this module is #included")
print("by the pcre2test program, which redefines the PRIV macro to change")
print("table names from _pcre2_xxx to xxxx, thereby avoiding name clashes")
print("with the library. At present, just one of these tables is actually")
print("needed. */")
print()
print("#ifndef PCRE2_PCRE2TEST")
print()
print("#ifdef HAVE_CONFIG_H")
print("#include \"config.h\"")
print("#endif")
print()
print("#include \"pcre2_internal.h\"")
print()
print("#endif /* PCRE2_PCRE2TEST */")
print()
print("/* Unicode character database. */")
print("/* This file was autogenerated by the MultiStage2.py script. */")
print("/* Total size: %d bytes, block size: %d. */" % (min_size, min_block_size))
print()
print("/* The tables herein are needed only when UCP support is built,")
print("and in PCRE2 that happens automatically with UTF support.") 
print("This module should not be referenced otherwise, so")
print("it should not matter whether it is compiled or not. However")
print("a comment was received about space saving - maybe the guy linked")
print("all the modules rather than using a library - so we include a")
print("condition to cut out the tables when not needed. But don't leave")
print("a totally empty module because some compilers barf at that.")
print("Instead, just supply small dummy tables. */")
print()
print("#ifndef SUPPORT_UNICODE")
print("const ucd_record PRIV(ucd_records)[] = {{0,0,0,0,0 }};")
print("const uint8_t PRIV(ucd_stage1)[] = {0};")
print("const uint16_t PRIV(ucd_stage2)[] = {0};")
print("const uint32_t PRIV(ucd_caseless_sets)[] = {0};")
print("#else")
print()
print("const char *PRIV(unicode_version) = \"{}\";".format(unicode_version))
print()
print("/* If the 32-bit library is run in non-32-bit mode, character values")
print("greater than 0x10ffff may be encountered. For these we set up a")
print("special record. */")
print()
print("#if PCRE2_CODE_UNIT_WIDTH == 32")
print("const ucd_record PRIV(dummy_ucd_record)[] = {{")
print("  ucp_Common,    /* script */")
print("  ucp_Cn,        /* type unassigned */")
print("  ucp_gbOther,   /* grapheme break property */")
print("  0,             /* case set */")
print("  0,             /* other case */")
print("  }};")
print("#endif")
print()
print(record_struct)

# --- Added by PH: output the table of caseless character sets ---

print("const uint32_t PRIV(ucd_caseless_sets)[] = {")
print("  NOTACHAR,")
for s in sets:
  s = sorted(s)
  for x in s:
    print('  0x%04x,' % x, end=' ')
  print('  NOTACHAR,')   
print('};')
print()

# ------

print("/* When #included in pcre2test, we don't need this large table. */")
print()
print("#ifndef PCRE2_PCRE2TEST")
print()
print_records(records, record_size)
print_table(min_stage1, 'PRIV(ucd_stage1)')
print_table(min_stage2, 'PRIV(ucd_stage2)', min_block_size)
print("#if UCD_BLOCK_SIZE != %d" % min_block_size)
print("#error Please correct UCD_BLOCK_SIZE in pcre2_internal.h")
print("#endif")
print("#endif  /* SUPPORT_UNICODE */")
print()
print("#endif  /* PCRE2_PCRE2TEST */")

"""

# Three-stage tables:

# Find the optimum block size for 3-stage table
min_size = sys.maxint
for stage3_block in [2 ** i for i in range(2,6)]:
        stage_i, stage3 = compress_table(table, stage3_block)
        for stage2_block in [2 ** i for i in range(5,10)]:
                size = len(records) * 4
                stage1, stage2 = compress_table(stage_i, stage2_block)
                size += get_tables_size(stage1, stage2, stage3)
                # print "/* %5d / %3d  => %5d bytes */" % (stage2_block, stage3_block, size)
                if size < min_size:
                        min_size = size
                        min_stage1, min_stage2, min_stage3 = stage1, stage2, stage3
                        min_stage2_block, min_stage3_block = stage2_block, stage3_block

print "/* Total size: %d bytes" % min_size */
print_records(records)
print_table(min_stage1, 'ucd_stage1')
print_table(min_stage2, 'ucd_stage2', min_stage2_block)
print_table(min_stage3, 'ucd_stage3', min_stage3_block)

"""