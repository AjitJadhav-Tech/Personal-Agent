#let d = json(bytes(sys.inputs.data))
#let b = d.basics
#set document(title: b.name + " – Cover letter", author: b.name)
#set page(paper: "a4", margin: (x: 2.2cm, y: 2cm))
#set text(font: "New Computer Modern", size: 11pt, lang: "en")
#set par(justify: false, leading: 0.65em, spacing: 1.1em)

#text(size: 15pt, weight: "bold", b.name) \
#b.email · #b.phone · #b.linkedin

#v(1em)
#d.date

Re: #d.title, #d.company

#for p in d.paragraphs [
  #p

]
Sincerely, \
#b.name
