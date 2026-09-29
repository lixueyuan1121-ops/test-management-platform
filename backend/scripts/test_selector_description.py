import unittest
import json
from scripts.test_selector_change_status import SelectorStatusTest
from app.models import SelectorKey
from app.models.module_entry import ModuleEntry
from app.services.selector_history import revision
from app.services.selector_description import split_description, normalize_description

class DescriptionTest(SelectorStatusTest):
    def seed_group(self):
        self.db.add_all([SelectorKey(project_id=1,key='row',page='安全设置',desc='[连接器]-[安全设置]-[安全设置页]-[安全设置行(多个)]',frame='vm',candidates='[]'),SelectorKey(project_id=1,key='toggle',page='安全设置',desc='[连接器]-[安全设置]-[开关操作]-[安全设置开关]',frame='vm',candidates='[]')])
        self.db.add(ModuleEntry(project_id=1,sub_product='',page='安全设置',nav_keys='["navSettings"]'))
        self.db.commit()
        return self.db.query(SelectorKey).all()
    def test_batch_changes_first_segment_only(self):
        rows=self.seed_group(); before=[(r.id,r.page,r.desc,r.key,r.frame,r.candidates) for r in rows]
        response=self.client.post('/api/selectors/batch-page',json={'ids':[r.id for r in rows],'page':'设置'})
        self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()
        for rid,page,desc,key,frame,candidates in before:
            r=self.db.get(SelectorKey,rid)
            self.assertEqual(split_description(r.desc),['设置',*split_description(desc)[1:]])
            self.assertEqual((r.page,r.key,r.frame,r.candidates),(page,key,frame,candidates))
    def test_rename_updates_second_segment_and_module(self):
        rows=self.seed_group(); request={'project_id':1,'old_page':'安全设置','new_page':'账户安全','expected_revisions':{r.id:revision(r) for r in rows}}
        response=self.client.post('/api/selectors/rename-page',json=request)
        self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()
        for r in self.db.query(SelectorKey).all():
            self.assertEqual(r.page,'账户安全');self.assertEqual(split_description(r.desc)[:2],['连接器','账户安全'])
        self.assertEqual(self.db.query(ModuleEntry).one().page,'账户安全')
        self.assertEqual(self.client.post('/api/selectors/rename-page',json=request).status_code,409)
    def test_collision_stale_and_scope(self):
        rows=self.seed_group(); request={'project_id':1,'old_page':'安全设置','new_page':'部门','expected_revisions':{r.id:revision(r) for r in rows}}
        self.db.add(SelectorKey(project_id=1,key='department',page='部门',desc='[部门]-[部门]-[浏览]-[列表]',candidates='[]'));self.db.commit()
        self.assertEqual(self.client.post('/api/selectors/rename-page',json=request).status_code,409)
        self.db.rollback(); request['new_page']='安全';request['expected_revisions']={rows[0].id:'stale'}
        self.assertEqual(self.client.post('/api/selectors/rename-page',json=request).status_code,409)
        self.assertEqual(self.db.query(ModuleEntry).one().page,'安全设置')
    def test_new_descriptions_and_empty_batch_rejected(self):
        created=self.create();self.assertIsNotNone(split_description(created['desc']))
        self.assertEqual(self.client.post('/api/selectors/batch-page',json={'ids':[created['id']],'page':' '}).status_code,422)
        self.assertEqual(split_description(normalize_description('保存按钮','编辑页','save')),['编辑页','编辑页','页面操作','保存按钮'])

    def test_batch_description_each_segment_and_blank_preservation(self):
        rows=self.seed_group()
        fields=['navigation','page','scene','element']
        for index,field in enumerate(fields):
            self.db.expire_all()
            before={r.id:(split_description(r.desc),r.page,r.key,r.frame,r.candidates) for r in rows}
            payload={'ids':[r.id for r in rows], 'expected_revisions':{r.id:revision(r) for r in rows},field:'批量值'+str(index)}
            if field!='scene':payload['scene']='  '
            response=self.client.post('/api/selectors/batch-description',json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['data']['updated'],2)
            self.db.expire_all()
            for r in rows:
                parts,page,key,frame,candidates=before[r.id]
                parts[index]='批量值'+str(index)
                self.assertEqual(split_description(r.desc),parts)
                self.assertEqual((r.page,r.key,r.frame,r.candidates),(page,key,frame,candidates))
    def test_batch_description_four_fields_and_stale_atomicity(self):
        rows=self.seed_group()
        payload={'ids':[r.id for r in rows], 'expected_revisions':{r.id:revision(r) for r in rows},'navigation':'技能','page':'专家','scene':'切换分类','element':'分类按钮'}
        response=self.client.post('/api/selectors/batch-description',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()
        self.assertTrue(all(r.desc=='[技能]-[专家]-[切换分类]-[分类按钮]' for r in rows))
        before={r.id:r.desc for r in rows}
        payload['navigation']='不应写入'
        self.assertEqual(self.client.post('/api/selectors/batch-description',json=payload).status_code,409)
        self.db.expire_all();self.assertEqual({r.id:r.desc for r in rows},before)
    def test_batch_description_empty_invalid_and_overlong_are_atomic(self):
        rows=self.seed_group();before={r.id:r.desc for r in rows}
        base={'ids':[r.id for r in rows], 'expected_revisions':{r.id:revision(r) for r in rows}}
        for values in ({'navigation':'  '},{'element':'[按钮]'},{'navigation':'长'*250}):
            response=self.client.post('/api/selectors/batch-description',json={**base,**values})
            self.assertEqual(response.status_code,422,response.text)
            self.db.expire_all();self.assertEqual({r.id:r.desc for r in rows},before)

if __name__=='__main__':unittest.main()
