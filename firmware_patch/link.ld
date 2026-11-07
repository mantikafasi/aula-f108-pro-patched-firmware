SECTIONS { . = 0x1A000; .text : { *(.text*) *(.rodata*) } /DISCARD/ : { *(.ARM.*) *(.comment) } }
